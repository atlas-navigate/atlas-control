package com.atlascontrol.mobile.ui

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.provider.Settings
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.QrCodeScanner
import androidx.compose.material.icons.filled.Router
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.atlascontrol.mobile.*
import com.atlascontrol.mobile.network.ColorPairing
import com.atlascontrol.mobile.network.QrPayload
import com.atlascontrol.mobile.network.SetupCode

/**
 * First run: find the Atlas and pair with it once.
 *
 *   Atlas not set up yet: tap Connect → press the Atlas button → pick the
 *   colour its light shows → Atlas opens (this phone is its primary user).
 *   Atlas already set up: tap Connect → its primary user taps "Allow".
 *
 * The label's QR code or setup code is the backup while Atlas isn't set up
 * (a broken button, or colours that are hard to tell apart). Typing an
 * address is an advanced fallback.
 */
@Composable
fun OnboardingScreen(vm: ConnectionViewModel) {
    val nearby by vm.nearby.collectAsState()
    val searching by vm.searching.collectAsState()
    val target by vm.target.collectAsState()
    val error by vm.pairingError.collectAsState()
    val matchCode by vm.matchCode.collectAsState()
    val color by vm.color.collectAsState()
    val colorBusy by vm.colorBusy.collectAsState()
    val joinWifi by vm.joinWifi.collectAsState()
    val networkBlocked by vm.localNetworkBlocked.collectAsState()
    var scanning by remember { mutableStateOf(false) }
    var advanced by remember { mutableStateOf(false) }
    var address by remember { mutableStateOf("") }
    var code by remember { mutableStateOf("") }
    var wifiPasswordTyped by remember { mutableStateOf(false) }
    val context = LocalContext.current

    if (scanning) {
        Box(Modifier.fillMaxSize().background(AtlasBackground)) {
            QrScanner(onResult = { text ->
                scanning = false
                val label = QrPayload.parse(text)
                if (label != null) vm.pairWithLabel(label)
            })
            TextButton(onClick = { scanning = false }, modifier = Modifier.align(Alignment.BottomCenter).padding(24.dp)) {
                Text("Cancel", color = AtlasOnBg)
            }
        }
        return
    }

    Column(
        Modifier.fillMaxSize().background(AtlasBackground).statusBarsPadding()
            .verticalScroll(rememberScrollState()).padding(24.dp),
    ) {
        Text("Connect to your Atlas", fontSize = 24.sp, fontWeight = FontWeight.Bold, color = AtlasOnBg)
        Spacer(Modifier.height(6.dp))
        Text("You only do this once. No account or password is needed.", color = AtlasMuted, fontSize = 14.sp)
        Spacer(Modifier.height(20.dp))
        if (networkBlocked) {
            LocalNetworkPermissionCard(onRetry = vm::searchNearby)
            Spacer(Modifier.height(16.dp))
        }

        when {
            joinWifi != null -> JoinAtlasWifi(joinWifi!!.ssid ?: "Atlas", joinWifi!!.password, context, onCancel = vm::cancelPairing)
            matchCode != null -> ApprovalWait(matchCode!!, onCancel = vm::cancelPairing)
            color != null -> ColorCard(target?.info?.name ?: "Atlas", color!!, colorBusy, onPick = vm::pickColor,
                onRestart = vm::startColorPairing, onUseCode = vm::useSetupCode, onCancel = vm::cancelPairing)
            target != null -> {
                val t = target!!
                Card(colors = CardDefaults.cardColors(containerColor = AtlasSurface2), shape = RoundedCornerShape(14.dp)) {
                    Column(Modifier.padding(18.dp)) {
                        Text(t.info.name, fontSize = 18.sp, fontWeight = FontWeight.Bold, color = AtlasOnBg)
                        Spacer(Modifier.height(10.dp))
                        if (t.info.setupState == "owned") {
                            Text("This Atlas already has a primary user. Ask them to allow this phone — they'll get " +
                                "a prompt on their phone or computer.", color = AtlasOnSurface, fontSize = 14.sp)
                            Spacer(Modifier.height(10.dp))
                            Button(onClick = vm::askForApproval, modifier = Modifier.fillMaxWidth().height(48.dp)) {
                                Text("Ask to connect")
                            }
                            Spacer(Modifier.height(8.dp))
                            Text("Lost the primary user's phone? Holding the button on Atlas for 15 seconds, until its " +
                                "light turns red, resets Atlas to factory settings and erases its data.",
                                color = AtlasMuted, fontSize = 12.sp)
                        } else {
                            Text("Enter the setup code from the label on your Atlas:", color = AtlasOnSurface, fontSize = 14.sp)
                            Spacer(Modifier.height(8.dp))
                            OutlinedTextField(
                                value = code,
                                onValueChange = {
                                    wifiPasswordTyped = SetupCode.looksLikeWifiPassword(it)
                                    code = SetupCode.format(it)
                                },
                                singleLine = true, placeholder = { Text("XXXX-XXXX-XXXX") },
                                label = { Text("Setup code") },
                                supportingText = {
                                    Text(if (wifiPasswordTyped) "That's the Wi-Fi password. Use the code labelled Setup code."
                                         else "3 groups of 4 capitals and digits — not the Wi-Fi password")
                                },
                                isError = wifiPasswordTyped,
                                textStyle = LocalTextStyle.current.copy(fontFamily = FontFamily.Monospace, letterSpacing = 2.sp),
                                keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Characters,
                                    autoCorrectEnabled = false),
                                modifier = Modifier.fillMaxWidth(),
                            )
                            Spacer(Modifier.height(10.dp))
                            Button(onClick = { vm.pairWithCode(code) }, enabled = SetupCode.isComplete(code),
                                modifier = Modifier.fillMaxWidth().height(48.dp)) { Text("Connect") }
                            Spacer(Modifier.height(8.dp))
                            OutlinedButton(onClick = { scanning = true }, modifier = Modifier.fillMaxWidth()) {
                                Icon(Icons.Default.QrCodeScanner, null); Spacer(Modifier.width(8.dp)); Text("Scan the label instead")
                            }
                            if (t.info.hasButton) {
                                TextButton(onClick = vm::startColorPairing) { Text("Use the Atlas button instead") }
                            }
                        }
                        TextButton(onClick = vm::cancelPairing) { Text("Back") }
                    }
                }
            }
            else -> {
                Button(onClick = { scanning = true }, modifier = Modifier.fillMaxWidth().height(52.dp),
                    shape = RoundedCornerShape(12.dp)) {
                    Icon(Icons.Default.QrCodeScanner, null); Spacer(Modifier.width(8.dp))
                    Text("Scan the code on your Atlas", fontWeight = FontWeight.Bold)
                }
                Spacer(Modifier.height(20.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("Atlas devices nearby", color = AtlasOnBg, fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                    if (searching) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                    else TextButton(onClick = vm::searchNearby) { Text("Search again") }
                }
                if (nearby.isEmpty() && !searching) {
                    Text("None found yet. Make sure Atlas is switched on, and that this phone is on the same Wi-Fi " +
                        "or on Atlas's own Wi-Fi — it is named after Atlas's address, like atlas-k7m2qx9wp.local " +
                        "(name and password are on the label).",
                        color = AtlasMuted, fontSize = 13.sp)
                }
                nearby.forEach { s ->
                    Card(
                        Modifier.fillMaxWidth().padding(top = 8.dp).clickable { vm.choose(s.baseUrl, s.pin) },
                        colors = CardDefaults.cardColors(containerColor = AtlasSurface2), shape = RoundedCornerShape(12.dp),
                    ) {
                        Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
                            Icon(Icons.Default.Router, null, tint = AtlasPrimary)
                            Spacer(Modifier.width(12.dp))
                            Column(Modifier.weight(1f)) {
                                Text(s.name, color = AtlasOnBg, fontWeight = FontWeight.SemiBold)
                                // Its own .local name (also on the label) tells two nearby Atlases apart.
                                Text(s.hostname ?: s.deviceId.take(8), color = AtlasMuted, fontSize = 12.sp)
                            }
                            Text("Connect", color = AtlasPrimary, fontWeight = FontWeight.Bold)
                        }
                    }
                }
                Spacer(Modifier.height(24.dp))
                TextButton(onClick = { advanced = !advanced }) { Text(if (advanced) "Hide advanced options" else "Advanced options") }
                if (advanced) {
                    TextButton(onClick = vm::scanNetwork) { Text("Scan this network for Atlas") }
                    OutlinedTextField(value = address, onValueChange = { address = it.trim() }, singleLine = true,
                        label = { Text("Atlas address") },
                        placeholder = { Text("atlas-7kq3pm.local or 192.168.1.20") },
                        supportingText = { Text("Its .local name is on the label and in Atlas's Settings.") },
                        keyboardOptions = KeyboardOptions(autoCorrectEnabled = false,
                            keyboardType = androidx.compose.ui.text.input.KeyboardType.Uri),
                        modifier = Modifier.fillMaxWidth())
                    TextButton(onClick = { if (address.isNotBlank()) vm.chooseTyped(address) }) {
                        Text("Connect to this address")
                    }
                }
            }
        }
        error?.let {
            Spacer(Modifier.height(12.dp))
            Text(it, color = AtlasError, fontSize = 14.sp)
        }
    }
}

private val COLOR_STEP_TEXT = mapOf(
    "conflict" to "Two devices tried to connect at the same time. Close Atlas on the other one, then start again.",
    "wrong" to "That wasn't the colour the light showed. Start again and press the button once more.",
    "expired" to "That took too long. Start again.",
)

private fun swatch(hex: String): Color =
    runCatching { Color(android.graphics.Color.parseColor(hex)) }.getOrDefault(Color.Gray)

/** Press the Atlas button, then pick the colour its light shows. */
@Composable
private fun ColorCard(
    name: String, p: ColorPairing, busy: Boolean,
    onPick: (String) -> Unit, onRestart: () -> Unit, onUseCode: () -> Unit, onCancel: () -> Unit,
) {
    Card(colors = CardDefaults.cardColors(containerColor = AtlasSurface2), shape = RoundedCornerShape(14.dp)) {
        Column(Modifier.padding(18.dp)) {
            Text(name, fontSize = 18.sp, fontWeight = FontWeight.Bold, color = AtlasOnBg)
            Spacer(Modifier.height(10.dp))
            when (p.state) {
                "press_button" -> {
                    Text("Press the button on your Atlas once.", color = AtlasOnBg, fontWeight = FontWeight.SemiBold)
                    Spacer(Modifier.height(6.dp))
                    Text("It's the round button on top of Atlas. Its light changes colour.", color = AtlasOnSurface, fontSize = 14.sp)
                    Spacer(Modifier.height(10.dp))
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
                        Spacer(Modifier.width(8.dp))
                        Text("Waiting for the button…", color = AtlasMuted, fontSize = 13.sp)
                    }
                }
                "choose_color" -> {
                    Text("Which colour is the button showing?", color = AtlasOnBg, fontWeight = FontWeight.SemiBold)
                    Spacer(Modifier.height(12.dp))
                    p.choices.chunked(3).forEach { row ->
                        Row(Modifier.fillMaxWidth().padding(bottom = 12.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            row.forEach { id ->
                                val c = p.palette.firstOrNull { it.id == id }
                                val label = c?.label ?: id
                                Column(
                                    Modifier.weight(1f).background(AtlasSurface, RoundedCornerShape(12.dp))
                                        .clickable(enabled = !busy) { onPick(id) }.padding(vertical = 10.dp)
                                        .semantics { contentDescription = label },
                                    horizontalAlignment = Alignment.CenterHorizontally,
                                ) {
                                    Box(Modifier.size(52.dp).background(swatch(c?.swatch ?: "#888888"), CircleShape)
                                        .border(2.dp, Color.Black.copy(alpha = 0.25f), CircleShape))
                                    Spacer(Modifier.height(6.dp))
                                    Text(label, color = AtlasOnBg, fontSize = 13.sp)
                                }
                            }
                            repeat(3 - row.size) { Spacer(Modifier.weight(1f)) }
                        }
                    }
                    Text("You get one pick. Only someone next to Atlas can see its light.", color = AtlasMuted, fontSize = 12.sp)
                }
                else -> {
                    Text(COLOR_STEP_TEXT[p.state] ?: "That attempt ended. Start again.", color = AtlasOnSurface, fontSize = 14.sp)
                    Spacer(Modifier.height(10.dp))
                    Button(onClick = onRestart, enabled = !busy, modifier = Modifier.fillMaxWidth().height(48.dp)) {
                        Text("Start again")
                    }
                }
            }
            TextButton(onClick = onUseCode) { Text("Can't use the button? Enter the setup code") }
            TextButton(onClick = onCancel) { Text("Back") }
        }
    }
}

@Composable
private fun ApprovalWait(code: String, onCancel: () -> Unit) {
    Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.fillMaxWidth()) {
        Text("On the primary user's phone or computer, tap Allow. Check both screens show:",
            color = AtlasOnSurface, textAlign = TextAlign.Center)
        Spacer(Modifier.height(16.dp))
        Text(code, fontSize = 40.sp, fontWeight = FontWeight.Bold, letterSpacing = 8.sp, color = AtlasOnBg,
            fontFamily = FontFamily.Monospace)
        Spacer(Modifier.height(10.dp))
        Text("If the codes differ, tap Don't allow — someone may be interfering with your network.",
            color = AtlasMuted, fontSize = 12.sp, textAlign = TextAlign.Center)
        Spacer(Modifier.height(16.dp))
        CircularProgressIndicator()
        TextButton(onClick = onCancel) { Text("Cancel") }
    }
}

@Composable
private fun JoinAtlasWifi(ssid: String, password: String?, context: Context, onCancel: () -> Unit) {
    Column {
        Text("Join Atlas's Wi-Fi", fontSize = 18.sp, fontWeight = FontWeight.Bold, color = AtlasOnBg)
        Spacer(Modifier.height(8.dp))
        Text("Connect this phone to the Wi-Fi network below. The password has been copied — paste it when asked. " +
            "Come back here afterwards; Atlas connects automatically.", color = AtlasOnSurface, fontSize = 14.sp)
        Spacer(Modifier.height(12.dp))
        Text(ssid, fontSize = 20.sp, fontWeight = FontWeight.Bold, color = AtlasPrimary)
        if (ssid.endsWith(".local")) {
            Text("Atlas's Wi-Fi is named after its address: once joined, Atlas is at $ssid.",
                color = AtlasMuted, fontSize = 12.sp)
        }
        LaunchedEffect(password) {
            if (password != null) {
                val cm = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                cm.setPrimaryClip(ClipData.newPlainText("Atlas Wi-Fi password", password))
            }
        }
        Spacer(Modifier.height(12.dp))
        Button(onClick = {
            runCatching { context.startActivity(Intent(Settings.Panel.ACTION_WIFI).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
        }, modifier = Modifier.fillMaxWidth()) { Text("Open Wi-Fi settings") }
        Spacer(Modifier.height(8.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
            Spacer(Modifier.width(8.dp))
            Text("Waiting for this phone to join $ssid…", color = AtlasMuted, fontSize = 13.sp)
        }
        TextButton(onClick = onCancel) { Text("Cancel") }
    }
}
