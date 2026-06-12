import json
import logging
import threading
import time
import traceback

try:
    import dbus
    import dbus.exceptions
    import dbus.mainloop.glib
    import dbus.service
    from gi.repository import GLib
    HAVE_DBUS = True
except Exception:
    dbus = None
    GLib = None
    HAVE_DBUS = False
    class _DummyService:
        class Object:
            def __init__(self, *args, **kwargs):
                pass

        @staticmethod
        def method(*args, **kwargs):
            def decorator(fn):
                return fn
            return decorator

    class _DummyExceptions:
        class DBusException(Exception):
            pass

    class _DummyDBus:
        service = _DummyService
        exceptions = _DummyExceptions

        @staticmethod
        def Array(values, signature=None):
            return list(values)

        @staticmethod
        def String(value):
            return str(value)

        @staticmethod
        def ObjectPath(value):
            return value

        class ByteArray(bytes):
            pass

    dbus = _DummyDBus()


BLUEZ_SERVICE_NAME = "org.bluez"
DBUS_OM_IFACE = "org.freedesktop.DBus.ObjectManager"
DBUS_PROP_IFACE = "org.freedesktop.DBus.Properties"
LE_ADVERTISING_MANAGER_IFACE = "org.bluez.LEAdvertisingManager1"
GATT_MANAGER_IFACE = "org.bluez.GattManager1"
GATT_SERVICE_IFACE = "org.bluez.GattService1"
GATT_CHRC_IFACE = "org.bluez.GattCharacteristic1"
LE_ADVERTISEMENT_IFACE = "org.bluez.LEAdvertisement1"

ATLAS_BLE_SERVICE_UUID = "7d2ea28a-3d2b-4f7b-9f10-9e4d4d6a0001"
ATLAS_BLE_MANIFEST_UUID = "7d2ea28a-3d2b-4f7b-9f10-9e4d4d6a0002"


def _to_dbus_array(values):
    return dbus.Array([dbus.String(v) for v in values], signature="s")


def _find_adapter(bus):
    manager = dbus.Interface(bus.get_object(BLUEZ_SERVICE_NAME, "/"), DBUS_OM_IFACE)
    objects = manager.GetManagedObjects()
    for path, ifaces in objects.items():
        if GATT_MANAGER_IFACE in ifaces and LE_ADVERTISING_MANAGER_IFACE in ifaces:
            return path
    raise RuntimeError("No BlueZ adapter with GATT/advertising support found")


class Application(dbus.service.Object):
    def __init__(self, bus):
        self.path = "/com/atlascontrol/mobile"
        self.services = []
        super().__init__(bus, self.path)

    def get_path(self):
        return dbus.ObjectPath(self.path)

    def add_service(self, service):
        self.services.append(service)

    @dbus.service.method(DBUS_OM_IFACE, out_signature="a{oa{sa{sv}}}")
    def GetManagedObjects(self):
        response = {}
        for service in self.services:
            response[service.get_path()] = service.get_properties()
            for characteristic in service.characteristics:
                response[characteristic.get_path()] = characteristic.get_properties()
        return response


class Service(dbus.service.Object):
    PATH_BASE = "/com/atlascontrol/mobile/service"

    def __init__(self, bus, index, uuid, primary):
        self.path = self.PATH_BASE + str(index)
        self.bus = bus
        self.uuid = uuid
        self.primary = primary
        self.characteristics = []
        super().__init__(bus, self.path)

    def get_properties(self):
        return {
            GATT_SERVICE_IFACE: {
                "UUID": self.uuid,
                "Primary": self.primary,
                "Characteristics": dbus.Array(
                    [c.get_path() for c in self.characteristics],
                    signature="o",
                ),
            }
        }

    def get_path(self):
        return dbus.ObjectPath(self.path)

    def add_characteristic(self, characteristic):
        self.characteristics.append(characteristic)

    @dbus.service.method(DBUS_PROP_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        if interface != GATT_SERVICE_IFACE:
            raise dbus.exceptions.DBusException("org.freedesktop.DBus.Error.InvalidArgs")
        return self.get_properties()[GATT_SERVICE_IFACE]


class Characteristic(dbus.service.Object):
    def __init__(self, bus, index, uuid, flags, service):
        self.path = service.path + "/char" + str(index)
        self.bus = bus
        self.uuid = uuid
        self.flags = flags
        self.service = service
        super().__init__(bus, self.path)

    def get_properties(self):
        return {
            GATT_CHRC_IFACE: {
                "Service": self.service.get_path(),
                "UUID": self.uuid,
                "Flags": _to_dbus_array(self.flags),
            }
        }

    def get_path(self):
        return dbus.ObjectPath(self.path)

    @dbus.service.method(DBUS_PROP_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        if interface != GATT_CHRC_IFACE:
            raise dbus.exceptions.DBusException("org.freedesktop.DBus.Error.InvalidArgs")
        return self.get_properties()[GATT_CHRC_IFACE]

    @dbus.service.method(GATT_CHRC_IFACE, in_signature="a{sv}", out_signature="ay")
    def ReadValue(self, options):
        raise NotImplementedError()


class Advertisement(dbus.service.Object):
    PATH_BASE = "/com/atlascontrol/mobile/advertisement"

    def __init__(self, bus, index, local_name, service_uuid):
        self.path = self.PATH_BASE + str(index)
        self.bus = bus
        self.local_name = local_name
        self.service_uuid = service_uuid
        super().__init__(bus, self.path)

    def get_path(self):
        return dbus.ObjectPath(self.path)

    def get_properties(self):
        return {
            LE_ADVERTISEMENT_IFACE: {
                "Type": dbus.String("peripheral"),
                "ServiceUUIDs": _to_dbus_array([self.service_uuid]),
                "LocalName": dbus.String(self.local_name),
                "Includes": _to_dbus_array(["tx-power"]),
            }
        }

    @dbus.service.method(DBUS_PROP_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        if interface != LE_ADVERTISEMENT_IFACE:
            raise dbus.exceptions.DBusException("org.freedesktop.DBus.Error.InvalidArgs")
        return self.get_properties()[LE_ADVERTISEMENT_IFACE]

    @dbus.service.method(LE_ADVERTISEMENT_IFACE, in_signature="", out_signature="")
    def Release(self):
        return


class BootstrapCharacteristic(Characteristic):
    def __init__(self, bus, index, service, bridge):
        super().__init__(bus, index, ATLAS_BLE_MANIFEST_UUID, ["read"], service)
        self.bridge = bridge

    @dbus.service.method(GATT_CHRC_IFACE, in_signature="a{sv}", out_signature="ay")
    def ReadValue(self, options):
        return dbus.ByteArray(self.bridge.manifest_bytes())


class AtlasBlePeripheral:
    def __init__(self, bridge, logger=None):
        self.bridge = bridge
        self.logger = logger or logging.getLogger("atlas-mobile-ble")
        self.bus = None
        self.mainloop = None
        self.adapter_path = None
        self.app = None
        self.advertisement = None
        self.running = False

    def start(self):
        if not HAVE_DBUS:
            raise RuntimeError("dbus/gi not available")
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.bus = dbus.SystemBus()
        self.adapter_path = _find_adapter(self.bus)
        self.app = Application(self.bus)
        service = Service(self.bus, 0, ATLAS_BLE_SERVICE_UUID, True)
        service.add_characteristic(BootstrapCharacteristic(self.bus, 0, service, self.bridge))
        self.app.add_service(service)
        self.advertisement = Advertisement(
            self.bus,
            0,
            self.bridge.local_name(),
            ATLAS_BLE_SERVICE_UUID,
        )

        service_manager = dbus.Interface(
            self.bus.get_object(BLUEZ_SERVICE_NAME, self.adapter_path),
            GATT_MANAGER_IFACE,
        )
        advertising_manager = dbus.Interface(
            self.bus.get_object(BLUEZ_SERVICE_NAME, self.adapter_path),
            LE_ADVERTISING_MANAGER_IFACE,
        )
        service_manager.RegisterApplication(
            self.app.get_path(),
            {},
            reply_handler=self._on_register_ok,
            error_handler=self._on_register_error,
        )
        advertising_manager.RegisterAdvertisement(
            self.advertisement.get_path(),
            {},
            reply_handler=self._on_advertisement_ok,
            error_handler=self._on_advertisement_error,
        )
        self.mainloop = GLib.MainLoop()
        self.running = True
        self.mainloop.run()

    def stop(self):
        self.running = False
        if self.mainloop is not None:
            self.mainloop.quit()

    def _on_register_ok(self):
        self.logger.info("Atlas mobile BLE GATT application registered")

    def _on_advertisement_ok(self):
        self.logger.info("Atlas mobile BLE advertisement active on %s", self.adapter_path)

    def _on_register_error(self, exc):
        self.logger.error("Atlas mobile BLE GATT registration failed: %s", exc)

    def _on_advertisement_error(self, exc):
        self.logger.error("Atlas mobile BLE advertisement failed: %s", exc)


class AtlasMobileBridge:
    def __init__(self, manifest_provider, logger=None):
        self.manifest_provider = manifest_provider
        self.logger = logger or logging.getLogger("atlas-mobile")
        self._peripheral = None
        self._thread = None
        self._last_error = None

    def local_name(self):
        try:
            manifest = self.manifest_provider()
            device = manifest.get("device") or {}
            name = device.get("name") or "Atlas Control"
            return str(name)[:24]
        except Exception:
            return "Atlas Control"

    def manifest(self):
        manifest = self.manifest_provider()
        manifest.setdefault("bluetooth", {})
        manifest["bluetooth"].update(
            {
                "serviceUuid": ATLAS_BLE_SERVICE_UUID,
                "manifestCharacteristicUuid": ATLAS_BLE_MANIFEST_UUID,
            }
        )
        return manifest

    def manifest_bytes(self):
        return json.dumps(
            self.manifest(),
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    def start(self):
        if not HAVE_DBUS:
            self._last_error = "dbus/gi unavailable"
            self.logger.warning("Bluetooth bootstrap disabled: %s", self._last_error)
            return False
        if self._thread and self._thread.is_alive():
            return True

        def _run():
            backoff_seconds = 15
            while True:
                try:
                    self._peripheral = AtlasBlePeripheral(self, logger=self.logger)
                    self._peripheral.start()
                    self._last_error = None
                    return
                except Exception as exc:
                    self._last_error = str(exc)
                    self.logger.error("Bluetooth bootstrap failed: %s", exc)
                    self.logger.debug(traceback.format_exc())
                    self.logger.info("Retrying Bluetooth bootstrap in %ss", backoff_seconds)
                    time.sleep(backoff_seconds)

        self._thread = threading.Thread(
            target=_run,
            daemon=True,
            name="atlas-mobile-ble",
        )
        self._thread.start()
        return True

    def status(self):
        return {
            "dbusAvailable": HAVE_DBUS,
            "running": bool(self._peripheral and self._peripheral.running),
            "lastError": self._last_error,
            "serviceUuid": ATLAS_BLE_SERVICE_UUID,
            "manifestCharacteristicUuid": ATLAS_BLE_MANIFEST_UUID,
        }
