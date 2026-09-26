package com.atlascontrol.mobile.ui

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.provider.Settings
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.atlascontrol.mobile.*
import com.atlascontrol.mobile.network.QrPayload
import com.atlascontrol.mobile.network.SetupCode

/**
 * First run: find the Atlas and pair with it once.
 *
 *   Open app → Atlas appears → tap Connect → (one more tap / scan) → Atlas opens.
 *
 * On Atlas's own Wi-Fi a single tap pairs. Elsewhere the owner scans the QR
 * code on the label (or types its setup code), or asks someone already using
 * this Atlas to tap "Allow". Typing an address is an advanced fallback.
 */
@Composable
fun OnboardingScreen(vm: ConnectionViewModel) {
    val nearby by vm.nearby.collectAsState()
    val searching by vm.searching.collectAsState()
    val target by vm.target.collectAsState()
    val error by vm.pairingError.collectAsState()
    val matchCode by vm.matchCode.collectAsState()
    val joinWifi by vm.joinWifi.collectAsState()
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

        when {
            joinWifi != null -> JoinAtlasWifi(joinWifi!!.ssid ?: "Atlas", joinWifi!!.password, context, onCancel = vm::cancelPairing)
            matchCode != null -> ApprovalWait(matchCode!!, onCancel = vm::cancelPairing)
            target != null -> {
                val t = target!!
                Card(colors = CardDefaults.cardColors(containerColor = AtlasSurface2), shape = RoundedCornerShape(14.dp)) {
                    Column(Modifier.padding(18.dp)) {
                        Text(t.info.name, fontSize = 18.sp, fontWeight = FontWeight.Bold, color = AtlasOnBg)
                        Spacer(Modifier.height(10.dp))
                        if (t.onAtlasHotspot) {
                            Text("Connecting…", color = AtlasMuted)
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
                            TextButton(onClick = vm::askForApproval) { Text("Ask someone using this Atlas to allow this phone") }
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
                        "or on Atlas's own Wi-Fi (its name and password are on the label).",
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
                                Text(s.deviceId.take(8), color = AtlasMuted, fontSize = 12.sp)
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
                        label = { Text("Atlas address (e.g. 192.168.1.20)") }, modifier = Modifier.fillMaxWidth())
                    TextButton(onClick = {
                        val a = address.removePrefix("https://").removePrefix("http://").trimEnd('/')
                        if (a.isNotBlank()) vm.choose("https://$a")
                    }) { Text("Connect to this address") }
                }
            }
        }
        error?.let {
            Spacer(Modifier.height(12.dp))
            Text(it, color = AtlasError, fontSize = 14.sp)
        }
    }
}

@Composable
private fun ApprovalWait(code: String, onCancel: () -> Unit) {
    Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.fillMaxWidth()) {
        Text("On a phone or computer already using this Atlas, tap Allow. Check both screens show:",
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
