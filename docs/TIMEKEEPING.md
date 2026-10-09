# Atlas's clock

Atlas has to work fully offline, including right after it has been unplugged.
This explains what sets its clock, what happens after power loss, and what
production units need.

## What was wrong

The Orin Nano's real-time clock is the PMIC RTC (`rtc0`, nvvrs-pseq-rtc). On
units without a backup cell it loses power with the unit and restarts from
**1970**. Its driver registers about 15 s into boot. At that point the kernel
sets the system clock from it ("hctosys"), and that comes *after* two earlier
corrections:

| Boot time | Event |
|---|---|
| 11.9 s | systemd advances the clock to its build date |
| ~15.3 s | systemd-timesyncd restores the time it saved (`time-set.target` reached) |
| 15.76 s | `nvvrs-pseq-rtc: setting system clock to 1970-01-01T00:00:34` |
| 17.7 s | avahi, NetworkManager, Atlas start in 1970 |
| +11 s | online only: NTP steps the clock forward 56 years |

Offline, a whole session ran in 1970: Ray said it was January 1970, logs and
records were stamped 1970, and every elapsed-time check that used the wall
clock was wrong. avahi's timers follow the wall clock, so a backward step
while it runs delays its announcements by the size of the step.

## What Atlas does now

`atlas-clock.service` (`atlas-engine clock boot`, `system/atlas_engine/clock.py`)
runs early in boot, before systemd-timesyncd, avahi and the app:

1. **Wait for the RTC.** It waits until `rtc0` has registered and the kernel
   has made its hctosys step. The wait is bounded (until 20 s of uptime) and
   skipped on boards without that RTC. On this hardware it costs nothing:
   avahi and NetworkManager already started later than that.
2. **Floor the clock.** The clock is never moved backward. If it is below the
   floor, it is moved forward to it. The floor is the later of:
   - the release's build time;
   - the time systemd-timesyncd last saved, which it does every 60 s, even
     offline, and on shutdown.

   systemd-timesyncd, which starts next, restores its own saved time on top.
3. **Record the clock's basis** in `/run/atlas-clock/state.json`:

| State | Meaning |
|---|---|
| `ntp` | synchronized with internet time (read live from systemd-timesyncd) |
| `gps` | set from a confirmed GPS time solution |
| `phone` | set from a paired phone's or browser's clock |
| `rtc` | the RTC kept time across the power cut (backup cell present) |
| `restored` | the last saved time: a lower bound; the real time is later |
| `unset` | nothing better than the build date |

A udev rule (`atlas-clock-rtc.service`) covers an RTC that registers after
the wait. It moves the clock forward again and restarts avahi.

**Discovery never waits for time.** avahi waits only for this short local
step, never for `time-sync.target`, NTP or a network. Atlas is discoverable
and usable fully offline, at whatever the clock says.

## Better time when Atlas gets it

Sources, best first:

1. **Internet time (NTP).** systemd-timesyncd. It always wins; nothing below
   overrides it while it is synchronized.
2. **GPS.** The u-blox receiver's UTC is used only when it vouches for it:
   - UBX NAV-PVT `validDate`, `validTime` and `fullyResolved` are all set;
   - the confirmed date and time flags are set, or, on receivers without
     them, there is a GNSS fix;
   - accuracy is better than 1 s;
   - it is not before 2025, which rejects week-number rollover dates;
   - NMEA RMC with status A also counts.

   The app offers it to the root helper (`clock.set`) at most every 10 minutes
   while the clock is unverified, and every 6 hours to correct drift. GPS may
   step the clock in either direction.
3. **A paired phone or browser.** When one connects while the clock is not
   verified, it sends its own clock. Atlas uses it only to move the clock
   **forward**, never past build time + 20 years, and never over GPS or NTP.
4. **The saved clock or the build date**, as above.

After any backward step, avahi is restarted so its announcements don't wait
out the difference.

## What else changed so a corrected clock can't break anything

- **Durations use the monotonic clock.** This covers:
  - Wi-Fi failover and hotspot grace;
  - Wi-Fi wait loops;
  - update checks;
  - GPS throttles and I2C deadlines;
  - the network-address cache;
  - the UPS reading cache;
  - Ray's timings.

  A clock step no longer stalls them or fires them early.
- **Pair requests** "created" in the future (before a backward correction)
  count as expired. They cannot stay approvable.
- **Rate-limit windows** reset after a backward step.
- **Retention purges** (location history, trackers, explanations, logs) wait
  while the clock came only from a phone. A restored clock is behind the real
  time, so it can only delete less, and purges run then.
- **Scheduled jobs** don't fire while the clock is unset.
- **Ray** says the date is "approximately …" after a restore, or unknown,
  instead of giving seasonal advice for January 1970.
- **The TLS certificate** is valid 2024–2099, so a wrong clock never
  invalidates it.

## Hardware recommendation for production units

Software can't make up time it never saw: after a long outage with no internet,
GPS fix or paired phone, the clock is only the last saved time. For accurate
offline time from the first second:

1. **Fit a backup cell to the RTC.** The Jetson module's RTC input (VCC_RTC)
   accepts a coin cell or supercapacitor. The developer-kit carrier has a
   2-pin RTC battery connector for it. Choose the cell (rechargeable ML-type
   vs primary CR-type) according to the carrier's design guide for the
   PMIC's backup charger. A unit with a cell reports `rtc_status:
   "kept-time"` after a power cut.
2. **Keep the GPS backup supply (V_BCKP).** Most u-blox boards have a small
   battery or supercap. It gives a hot start, and a confirmed time within
   seconds of sky view.
3. **Test it on the line.** After the unit's first boot, unplug it for a
   minute and boot again. `atlas-engine clock status` must show
   `"status": "kept-time"` for the RTC. `"lost-power"` means the cell is
   missing or flat.

## Checking a unit

    atlas-engine clock status           # root: state, floor, RTC status, NTP
    cat /run/atlas-clock/state.json     # anyone
    journalctl -b -k | grep -i rtc      # when the RTC registered and what it read

The app shows the clock's basis in Settings → Local Device Address whenever
it is not verified, and serves it in `/api/device/local` (`clock`).
