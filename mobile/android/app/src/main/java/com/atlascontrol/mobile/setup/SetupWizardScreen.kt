package com.atlascontrol.mobile.setup

import android.content.Intent
import android.provider.Settings
import androidx.compose.animation.*
import androidx.compose.animation.core.*
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.scale
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.atlascontrol.mobile.*
import com.atlascontrol.mobile.network.BootstrapManifest
import com.atlascontrol.mobile.ui.AppViewModel

// ─── Root composable ──────────────────────────────────────────────────────────

@Composable
fun SetupWizardScreen(
    setupVm: SetupViewModel,
    appVm: AppViewModel,
) {
    val step by setupVm.step.collectAsState()

    Box(
        Modifier
            .fillMaxSize()
            .background(Brush.verticalGradient(listOf(AtlasBackground, AtlasSurface)))
            .safeDrawingPadding()
    ) {
        AnimatedContent(
            targetState = step,
            transitionSpec = { slideInHorizontally { it } + fadeIn() togetherWith slideOutHorizontally { -it } + fadeOut() },
            label = "setup_wizard",
        ) { s ->
            when (s) {
                SetupStep.WELCOME         -> WelcomeStep(onNext = { setupVm.goToHotspotStep() })
                SetupStep.HOTSPOT_CONNECT -> HotspotConnectStep(setupVm)
                SetupStep.PAIRING         -> PairingStep(setupVm, appVm)
                SetupStep.LAN_PROVISION   -> LanProvisionStep(setupVm, appVm)
                SetupStep.DONE            -> { /* AppViewModel drives navigation from here */ }
            }
        }
    }
}

// ─── Step 1 — Welcome ─────────────────────────────────────────────────────────

@Composable
private fun WelcomeStep(onNext: () -> Unit) {
    WizardColumn {
        Spacer(Modifier.height(40.dp))

        Icon(
            Icons.Default.Hub,
            contentDescription = null,
            tint     = AtlasPrimary,
            modifier = Modifier.size(88.dp).align(Alignment.CenterHorizontally),
        )
        Spacer(Modifier.height(24.dp))

        Text(
            "Atlas Control",
            fontSize   = 34.sp,
            fontWeight = FontWeight.ExtraBold,
            color      = AtlasOnBg,
            modifier   = Modifier.align(Alignment.CenterHorizontally),
        )
        Text(
            "Your offline field cyberdeck",
            fontSize = 15.sp,
            color    = AtlasMuted,
            modifier = Modifier.align(Alignment.CenterHorizontally),
        )

        Spacer(Modifier.height(36.dp))
        AtlasCard {
            Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
                FeatureLine(Icons.Default.Map,           "Offline maps + OSRM routing — no internet needed")
                FeatureLine(Icons.Default.Hub,           "Meshtastic mesh radio — text and position sharing")
                FeatureLine(Icons.Default.Psychology,    "Local AI assistant (Ollama) — runs entirely on-device")
                FeatureLine(Icons.Default.GpsFixed,      "GPS tracking with UBX dead-reckoning")
                FeatureLine(Icons.Default.Wifi,          "Hotspot + LAN — connects wherever Atlas can be reached")
            }
        }

        Spacer(Modifier.height(36.dp))

        AtlasCard {
            Column(Modifier.padding(16.dp)) {
                Text("How setup works", fontWeight = FontWeight.SemiBold, color = AtlasOnBg, fontSize = 14.sp)
                Spacer(Modifier.height(10.dp))
                SetupStep("1", "Connect your phone to the Atlas WiFi hotspot")
                Spacer(Modifier.height(8.dp))
                SetupStep("2", "The app finds Atlas and saves the connection")
                Spacer(Modifier.height(8.dp))
                SetupStep("3", "Atlas can also join your home / office LAN — both networks work automatically")
            }
        }

        Spacer(Modifier.height(36.dp))
        PrimaryButton("Get Started", onClick = onNext)
        Spacer(Modifier.height(32.dp))
    }
}

@Composable
private fun SetupStep(number: String, text: String) {
    Row(verticalAlignment = Alignment.Top) {
        Box(
            Modifier
                .size(24.dp)
                .background(AtlasPrimary.copy(alpha = 0.15f), CircleShape),
            contentAlignment = Alignment.Center,
        ) {
            Text(number, color = AtlasPrimary, fontSize = 12.sp, fontWeight = FontWeight.Bold)
        }
        Spacer(Modifier.width(10.dp))
        Text(text, color = AtlasOnSurface, fontSize = 13.sp, modifier = Modifier.weight(1f))
    }
}

// ─── Step 2 — Hotspot connect ─────────────────────────────────────────────────

@Composable
private fun HotspotConnectStep(setupVm: SetupViewModel) {
    val context         = LocalContext.current
    val isSearching     by setupVm.isSearching.collectAsState()
    val discovery       by setupVm.discovery.collectAsState()
    val errorMsg        by setupVm.errorMsg.collectAsState()
    val hotspotSsid     by setupVm.hotspotSsid.collectAsState()
    val hotspotPassword by setupVm.hotspotPassword.collectAsState()
    var showAdvanced    by remember { mutableStateOf(false) }
    var manualUrl       by remember { mutableStateOf(setupVm.manualUrl.value) }

    // Start scanning automatically when this step is shown
    LaunchedEffect(Unit) {
        if (!isSearching && discovery == null) {
            setupVm.startHotspotSearch()
        }
    }

    // Pulse animation for the WiFi icon
    val infiniteTransition = rememberInfiniteTransition(label = "pulse")
    val pulse by infiniteTransition.animateFloat(
        initialValue = 1f,
        targetValue  = 1.12f,
        animationSpec = infiniteRepeatable(
            animation = tween(900, easing = FastOutSlowInEasing),
            repeatMode = RepeatMode.Reverse,
        ),
        label = "pulse_scale",
    )

    WizardColumn {
        Spacer(Modifier.height(36.dp))

        Icon(
            Icons.Default.WifiTethering,
            contentDescription = null,
            tint     = if (isSearching) AtlasTertiary else AtlasPrimary,
            modifier = Modifier
                .size(80.dp)
                .align(Alignment.CenterHorizontally)
                .then(if (isSearching) Modifier.scale(pulse) else Modifier),
        )
        Spacer(Modifier.height(20.dp))

        Text(
            "Connect to Atlas Hotspot",
            fontSize   = 26.sp,
            fontWeight = FontWeight.Bold,
            color      = AtlasOnBg,
            modifier   = Modifier.align(Alignment.CenterHorizontally),
        )
        Spacer(Modifier.height(8.dp))
        Text(
            "Connect your phone to the Atlas WiFi network, then this app will find Atlas automatically.",
            color    = AtlasMuted,
            fontSize = 14.sp,
            textAlign = TextAlign.Center,
        )

        Spacer(Modifier.height(28.dp))

        // ── Network credentials card ──────────────────────────────────────────
        AtlasCard {
            Column(Modifier.padding(18.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.Default.Wifi, null, tint = AtlasSecondary, modifier = Modifier.size(22.dp))
                    Spacer(Modifier.width(10.dp))
                    Text("Atlas Hotspot", fontWeight = FontWeight.SemiBold, color = AtlasOnBg)
                }
                Spacer(Modifier.height(14.dp))
                CredentialRow("Network (SSID)", hotspotSsid)
                Spacer(Modifier.height(8.dp))
                CredentialRow("Password", hotspotPassword)
                Spacer(Modifier.height(16.dp))
                OutlinedButton(
                    onClick = {
                        context.startActivity(
                            Intent(Settings.ACTION_WIFI_SETTINGS)
                                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                        )
                    },
                    modifier = Modifier.fillMaxWidth(),
                    shape  = RoundedCornerShape(10.dp),
                    colors = ButtonDefaults.outlinedButtonColors(contentColor = AtlasPrimary),
                ) {
                    Icon(Icons.Default.OpenInNew, null, modifier = Modifier.size(16.dp))
                    Spacer(Modifier.width(8.dp))
                    Text("Open WiFi Settings", fontSize = 14.sp)
                }
            }
        }

        Spacer(Modifier.height(20.dp))

        // ── Search status card ───────────────────────────────────────────────
        AtlasCard {
            Row(
                Modifier.padding(16.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                if (isSearching) {
                    CircularProgressIndicator(
                        color       = AtlasTertiary,
                        strokeWidth = 2.5.dp,
                        modifier    = Modifier.size(26.dp),
                    )
                } else {
                    Icon(
                        if (errorMsg != null) Icons.Default.ErrorOutline else Icons.Default.Search,
                        null,
                        tint     = if (errorMsg != null) AtlasError else AtlasMuted,
                        modifier = Modifier.size(26.dp),
                    )
                }
                Spacer(Modifier.width(12.dp))
                Column(Modifier.weight(1f)) {
                    Text(
                        if (isSearching) "Searching for Atlas…" else if (errorMsg != null) "Atlas not found" else "Ready to search",
                        fontWeight = FontWeight.SemiBold,
                        color      = AtlasOnBg,
                        fontSize   = 14.sp,
                    )
                    Text(
                        if (isSearching)
                            "Checking gateway and known hotspot IPs…"
                        else
                            "Once you join atlas_navigate, tap Search",
                        color    = AtlasMuted,
                        fontSize = 12.sp,
                    )
                }
            }
        }

        // ── Error message ─────────────────────────────────────────────────────
        AnimatedVisibility(errorMsg != null) {
            Column {
                Spacer(Modifier.height(12.dp))
                AtlasCard {
                    Row(
                        Modifier.padding(14.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Icon(Icons.Default.Warning, null, tint = AtlasError, modifier = Modifier.size(20.dp))
                        Spacer(Modifier.width(10.dp))
                        Text(
                            errorMsg ?: "",
                            color    = AtlasError,
                            fontSize = 13.sp,
                            modifier = Modifier.weight(1f),
                        )
                    }
                }
            }
        }

        Spacer(Modifier.height(20.dp))

        PrimaryButton(
            text    = if (isSearching) "Searching…" else "Search",
            enabled = !isSearching,
            icon    = if (!isSearching) Icons.Default.Search else null,
            onClick = { setupVm.retrySearch() },
        )

        Spacer(Modifier.height(16.dp))

        // ── Advanced / manual entry ───────────────────────────────────────────
        Row(
            Modifier
                .align(Alignment.CenterHorizontally)
                .clickable { showAdvanced = !showAdvanced }
                .padding(vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                if (showAdvanced) "Hide manual address" else "Enter address manually",
                color    = AtlasMuted,
                fontSize = 13.sp,
            )
            Spacer(Modifier.width(4.dp))
            Icon(
                if (showAdvanced) Icons.Default.ExpandLess else Icons.Default.ExpandMore,
                null,
                tint     = AtlasMuted,
                modifier = Modifier.size(18.dp),
            )
        }

        AnimatedVisibility(showAdvanced) {
            Column {
                Spacer(Modifier.height(4.dp))
                AtlasCard {
                    Column(Modifier.padding(16.dp)) {
                        Text(
                            "Manual address",
                            fontWeight = FontWeight.SemiBold,
                            color      = AtlasOnBg,
                            fontSize   = 14.sp,
                        )
                        Spacer(Modifier.height(4.dp))
                        Text(
                            "Use if Atlas is on a LAN with a known address.",
                            color    = AtlasMuted,
                            fontSize = 12.sp,
                        )
                        Spacer(Modifier.height(10.dp))
                        OutlinedTextField(
                            value       = manualUrl,
                            onValueChange = { manualUrl = it; setupVm.manualUrl.value = it },
                            placeholder = { Text("atlas.local  or  192.168.1.x", color = AtlasMuted) },
                            singleLine  = true,
                            keyboardOptions = KeyboardOptions(
                                keyboardType = KeyboardType.Uri,
                                imeAction    = ImeAction.Go,
                            ),
                            keyboardActions = KeyboardActions(
                                onGo = { setupVm.connectManual {} }
                            ),
                            colors = OutlinedTextFieldDefaults.colors(
                                focusedTextColor    = AtlasOnBg,
                                unfocusedTextColor  = AtlasOnBg,
                                focusedBorderColor  = AtlasPrimary,
                                unfocusedBorderColor = AtlasMuted.copy(alpha = 0.4f),
                                cursorColor         = AtlasPrimary,
                            ),
                            modifier = Modifier.fillMaxWidth(),
                        )
                        Spacer(Modifier.height(10.dp))
                        OutlinedButton(
                            onClick  = { setupVm.connectManual {} },
                            enabled  = !isSearching,
                            modifier = Modifier.fillMaxWidth(),
                            shape    = RoundedCornerShape(10.dp),
                            colors   = ButtonDefaults.outlinedButtonColors(contentColor = AtlasPrimary),
                        ) {
                            Text("Connect to this address", fontSize = 14.sp)
                        }
                    }
                }
            }
        }

        Spacer(Modifier.height(32.dp))
    }
}

// ─── Step 3 — Pairing / found ─────────────────────────────────────────────────

@Composable
private fun PairingStep(setupVm: SetupViewModel, appVm: AppViewModel) {
    val discovery by setupVm.discovery.collectAsState()
    val manifest  = discovery?.manifest ?: BootstrapManifest()

    val deviceName  = manifest.device?.name?.takeIf { it.isNotBlank() } ?: "Atlas Control"
    val shortName   = manifest.device?.shortName?.takeIf { it.isNotBlank() } ?: "ATLS"
    val caps        = manifest.capabilities ?: emptyMap()
    val hotspot     = manifest.hotspot
    val allUrls     = manifest.api?.baseUrls ?: listOfNotNull(discovery?.foundUrl)
    val foundUrl    = discovery?.foundUrl ?: ""

    WizardColumn {
        Spacer(Modifier.height(40.dp))

        // Success indicator
        Box(
            Modifier
                .size(88.dp)
                .background(AtlasSecondary.copy(alpha = 0.12f), CircleShape)
                .align(Alignment.CenterHorizontally),
            contentAlignment = Alignment.Center,
        ) {
            Icon(
                Icons.Default.CheckCircle,
                null,
                tint     = AtlasSecondary,
                modifier = Modifier.size(56.dp),
            )
        }
        Spacer(Modifier.height(20.dp))

        Text(
            "Atlas Found!",
            fontSize   = 28.sp,
            fontWeight = FontWeight.Bold,
            color      = AtlasOnBg,
            modifier   = Modifier.align(Alignment.CenterHorizontally),
        )
        Text(
            "Ready to connect — tap Open Atlas to launch the full interface.",
            fontSize  = 14.sp,
            color     = AtlasMuted,
            textAlign = TextAlign.Center,
        )

        Spacer(Modifier.height(28.dp))

        // ── Device info ───────────────────────────────────────────────────────
        AtlasCard {
            Column(Modifier.padding(18.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.Default.Hub, null, tint = AtlasPrimary, modifier = Modifier.size(20.dp))
                    Spacer(Modifier.width(8.dp))
                    Text("Device", fontWeight = FontWeight.SemiBold, color = AtlasOnBg)
                }
                Spacer(Modifier.height(12.dp))
                InfoRow("Name",       deviceName)
                InfoRow("Short name", shortName)
                if (foundUrl.isNotBlank()) InfoRow("Address", foundUrl)
            }
        }

        // ── Capabilities ──────────────────────────────────────────────────────
        if (caps.isNotEmpty()) {
            Spacer(Modifier.height(14.dp))
            AtlasCard {
                Column(Modifier.padding(18.dp)) {
                    Text("Capabilities", fontWeight = FontWeight.SemiBold, color = AtlasOnBg, fontSize = 14.sp)
                    Spacer(Modifier.height(12.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                        CapBadge("Mesh",  caps["mesh"] == true)
                        CapBadge("GPS",   caps["gps"] == true)
                        CapBadge("AI",    caps["ai"] == true)
                        CapBadge("Nav",   caps["navigation"] == true)
                        CapBadge("WiFi",  caps["wifi"] == true)
                    }
                }
            }
        }

        // ── Hotspot info (if active) ──────────────────────────────────────────
        if (hotspot?.active == true && hotspot.ssid.isNotBlank()) {
            Spacer(Modifier.height(14.dp))
            AtlasCard {
                Column(Modifier.padding(18.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Default.WifiTethering, null, tint = AtlasTertiary, modifier = Modifier.size(18.dp))
                        Spacer(Modifier.width(8.dp))
                        Text("Hotspot Active", fontWeight = FontWeight.SemiBold, color = AtlasOnBg, fontSize = 14.sp)
                    }
                    Spacer(Modifier.height(10.dp))
                    InfoRow("SSID",     hotspot.ssid)
                    if (hotspot.password.isNotBlank()) InfoRow("Password", hotspot.password)
                }
            }
        }

        // ── LAN URLs (informational) ──────────────────────────────────────────
        val lanUrls = allUrls.filter { !isHotspotUrl(it) }
        if (lanUrls.isNotEmpty()) {
            Spacer(Modifier.height(14.dp))
            AtlasCard {
                Column(Modifier.padding(18.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Default.Lan, null, tint = AtlasSecondary, modifier = Modifier.size(18.dp))
                        Spacer(Modifier.width(8.dp))
                        Text("LAN Access", fontWeight = FontWeight.SemiBold, color = AtlasOnBg, fontSize = 14.sp)
                    }
                    Spacer(Modifier.height(4.dp))
                    Text(
                        "Atlas is also reachable on your local network. The app will auto-switch between hotspot and LAN.",
                        color = AtlasMuted, fontSize = 12.sp,
                    )
                    Spacer(Modifier.height(10.dp))
                    lanUrls.take(3).forEach { url ->
                        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(vertical = 2.dp)) {
                            Icon(Icons.Outlined.CheckCircle, null, tint = AtlasSecondary, modifier = Modifier.size(14.dp))
                            Spacer(Modifier.width(6.dp))
                            Text(url, color = AtlasOnSurface, fontSize = 12.sp, fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace)
                        }
                    }
                }
            }
        }

        Spacer(Modifier.height(28.dp))

        PrimaryButton(
            text  = "Open Atlas",
            icon  = Icons.Default.OpenInBrowser,
            onClick = {
                setupVm.completeSetup(appVm)
                // AppViewModel.state will transition to CHECKING → CONNECTED, driving nav
            },
        )

        Spacer(Modifier.height(32.dp))
    }
}

// ─── Shared UI helpers ─────────────────────────────────────────────────────────

@Composable
private fun WizardColumn(content: @Composable ColumnScope.() -> Unit) {
    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 24.dp),
        content = content,
    )
}

@Composable
private fun AtlasCard(content: @Composable () -> Unit) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape    = RoundedCornerShape(14.dp),
        colors   = CardDefaults.cardColors(containerColor = AtlasSurface2),
        content  = { content() },
    )
}

@Composable
private fun PrimaryButton(
    text: String,
    enabled: Boolean = true,
    icon: ImageVector? = null,
    onClick: () -> Unit = {},
) {
    Button(
        onClick  = onClick,
        enabled  = enabled,
        modifier = Modifier.fillMaxWidth().height(54.dp),
        shape    = RoundedCornerShape(12.dp),
        colors   = ButtonDefaults.buttonColors(
            containerColor         = AtlasPrimary,
            contentColor           = AtlasBackground,
            disabledContainerColor = AtlasMuted.copy(alpha = 0.25f),
            disabledContentColor   = AtlasMuted.copy(alpha = 0.6f),
        ),
    ) {
        if (icon != null) {
            Icon(icon, null, modifier = Modifier.size(18.dp))
            Spacer(Modifier.width(8.dp))
        }
        Text(text, fontWeight = FontWeight.Bold, fontSize = 16.sp)
    }
}

@Composable
private fun FeatureLine(icon: ImageVector, text: String) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Icon(icon, null, tint = AtlasSecondary, modifier = Modifier.size(20.dp))
        Spacer(Modifier.width(12.dp))
        Text(text, color = AtlasOnSurface, fontSize = 14.sp, modifier = Modifier.weight(1f))
    }
}

@Composable
private fun CredentialRow(label: String, value: String) {
    Row(
        Modifier
            .fillMaxWidth()
            .background(AtlasBackground.copy(alpha = 0.5f), RoundedCornerShape(8.dp))
            .padding(horizontal = 12.dp, vertical = 10.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(label, color = AtlasMuted, fontSize = 13.sp)
        Text(
            value,
            color      = AtlasOnBg,
            fontWeight = FontWeight.SemiBold,
            fontSize   = 15.sp,
            fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace,
        )
    }
}

@Composable
private fun InfoRow(label: String, value: String) {
    Row(
        Modifier.fillMaxWidth().padding(vertical = 3.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment     = Alignment.CenterVertically,
    ) {
        Text(label, color = AtlasMuted, fontSize = 13.sp)
        Text(
            value,
            color      = AtlasOnBg,
            fontWeight = FontWeight.Medium,
            fontSize   = 13.sp,
            textAlign  = TextAlign.End,
            modifier   = Modifier.weight(1f, fill = false).padding(start = 8.dp),
        )
    }
}

@Composable
private fun CapBadge(label: String, active: Boolean) {
    Surface(
        shape  = RoundedCornerShape(6.dp),
        color  = if (active) AtlasSecondary.copy(alpha = 0.15f) else AtlasSurface2,
        border = BorderStroke(
            1.dp, if (active) AtlasSecondary.copy(alpha = 0.5f) else AtlasMuted.copy(alpha = 0.3f)
        ),
    ) {
        Text(
            label,
            color    = if (active) AtlasSecondary else AtlasMuted,
            fontSize = 11.sp,
            fontWeight = if (active) FontWeight.SemiBold else FontWeight.Normal,
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
        )
    }
}

// ─── Step 4 — LAN provisioning ────────────────────────────────────────────────

@Composable
private fun LanProvisionStep(setupVm: SetupViewModel, appVm: AppViewModel) {
    val context         = LocalContext.current
    val isConnecting    by setupVm.lanConnecting.collectAsState()
    val connectDone     by setupVm.lanConnectDone.collectAsState()
    val connectError    by setupVm.lanConnectError.collectAsState()
    val connectSsid     by setupVm.lanConnectSsid.collectAsState()
    val isHandoffPending by setupVm.lanHandoffPending.collectAsState()

    var ssid         by remember { mutableStateOf("") }
    var password     by remember { mutableStateOf("") }
    var showPassword by remember { mutableStateOf(false) }

    WizardColumn {
        Spacer(Modifier.height(36.dp))

        Icon(
            Icons.Default.Lan,
            contentDescription = null,
            tint     = AtlasPrimary,
            modifier = Modifier.size(72.dp).align(Alignment.CenterHorizontally),
        )
        Spacer(Modifier.height(16.dp))
        Text(
            "Connect Atlas to LAN",
            fontSize   = 26.sp,
            fontWeight = FontWeight.Bold,
            color      = AtlasOnBg,
            modifier   = Modifier.align(Alignment.CenterHorizontally),
        )
        Spacer(Modifier.height(6.dp))
        Text(
            "Enter the WiFi network Atlas should join. After Atlas leaves its hotspot, connect your phone to the same LAN and the app will stay paired to this Atlas.",
            color     = AtlasMuted,
            fontSize  = 14.sp,
            textAlign = TextAlign.Center,
        )

        Spacer(Modifier.height(24.dp))

        when {
            // ── Success ───────────────────────────────────────────────────────
            connectDone -> {
                Box(
                    Modifier
                        .size(72.dp)
                        .background(AtlasSecondary.copy(alpha = 0.12f), CircleShape)
                        .align(Alignment.CenterHorizontally),
                    contentAlignment = Alignment.Center,
                ) {
                    Icon(Icons.Default.CheckCircle, null, tint = AtlasSecondary, modifier = Modifier.size(44.dp))
                }
                Spacer(Modifier.height(14.dp))
                Text(
                    "Atlas joined $connectSsid",
                    color      = AtlasSecondary,
                    fontWeight = FontWeight.Bold,
                    fontSize   = 18.sp,
                    modifier   = Modifier.align(Alignment.CenterHorizontally),
                )
                Spacer(Modifier.height(6.dp))
                Text(
                    "Atlas is now reachable on $connectSsid. Your phone is connected to the same network.",
                    color     = AtlasMuted,
                    fontSize  = 13.sp,
                    textAlign = TextAlign.Center,
                )
                Spacer(Modifier.height(28.dp))
                PrimaryButton("Open Atlas", icon = Icons.Default.OpenInBrowser) {
                    setupVm.completeLanSetup(appVm)
                }
            }

            // ── Connecting spinner ─────────────────────────────────────────────
            isConnecting -> {
                AtlasCard {
                    Column(
                        Modifier.padding(24.dp),
                        horizontalAlignment = Alignment.CenterHorizontally,
                        verticalArrangement = Arrangement.spacedBy(16.dp),
                    ) {
                        CircularProgressIndicator(color = AtlasPrimary, modifier = Modifier.size(44.dp))
                        if (isHandoffPending) {
                            Text(
                                "Atlas is switching to $connectSsid…",
                                color      = AtlasOnBg,
                                fontWeight = FontWeight.SemiBold,
                                textAlign  = TextAlign.Center,
                            )
                            Text(
                                "Atlas dropped its hotspot to join $connectSsid. Connect your phone to \"$connectSsid\" using the button below — the app will reconnect automatically.",
                                color     = AtlasMuted,
                                fontSize  = 12.sp,
                                textAlign = TextAlign.Center,
                            )
                        } else {
                            Text(
                                "Connecting Atlas to $connectSsid…",
                                color      = AtlasOnBg,
                                fontWeight = FontWeight.SemiBold,
                                textAlign  = TextAlign.Center,
                            )
                            Text(
                                "Sending request to Atlas…",
                                color     = AtlasMuted,
                                fontSize  = 12.sp,
                                textAlign = TextAlign.Center,
                            )
                        }
                    }
                }
                if (isHandoffPending) {
                    Spacer(Modifier.height(12.dp))
                    OutlinedButton(
                        onClick = {
                            context.startActivity(
                                Intent(Settings.ACTION_WIFI_SETTINGS)
                                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                            )
                        },
                        modifier = Modifier.fillMaxWidth().height(52.dp),
                        shape    = RoundedCornerShape(12.dp),
                        colors   = ButtonDefaults.outlinedButtonColors(contentColor = AtlasPrimary),
                    ) {
                        Icon(Icons.Default.Wifi, null, modifier = Modifier.size(18.dp))
                        Spacer(Modifier.width(8.dp))
                        Text("Switch to $connectSsid in WiFi Settings", fontWeight = FontWeight.Medium, fontSize = 14.sp)
                    }
                }
            }

            // ── Network picker + password ──────────────────────────────────────
            else -> {
                connectError?.let { err ->
                    AtlasCard {
                        Row(Modifier.padding(14.dp), verticalAlignment = Alignment.CenterVertically) {
                            Icon(Icons.Default.Warning, null, tint = AtlasError, modifier = Modifier.size(20.dp))
                            Spacer(Modifier.width(10.dp))
                            Text(err, color = AtlasError, fontSize = 13.sp, modifier = Modifier.weight(1f))
                        }
                    }
                    Spacer(Modifier.height(14.dp))
                }

                AtlasCard {
                    Column(Modifier.padding(16.dp)) {
                        Text(
                            "LAN credentials",
                            fontWeight = FontWeight.SemiBold,
                            color      = AtlasOnBg,
                            fontSize   = 14.sp,
                        )
                        Spacer(Modifier.height(6.dp))
                        Text(
                            "Use the exact SSID and password for the LAN you want Atlas to join. Leave the password blank only for an open network.",
                            color    = AtlasMuted,
                            fontSize = 12.sp,
                        )
                        Spacer(Modifier.height(12.dp))
                        OutlinedTextField(
                            value         = ssid,
                            onValueChange = { ssid = it },
                            label         = { Text("LAN SSID") },
                            placeholder   = { Text("Example: ShopNet or Home WiFi", color = AtlasMuted) },
                            singleLine    = true,
                            keyboardOptions = KeyboardOptions(
                                keyboardType = KeyboardType.Text,
                                imeAction    = ImeAction.Next,
                            ),
                            colors = OutlinedTextFieldDefaults.colors(
                                focusedTextColor     = AtlasOnBg,
                                unfocusedTextColor   = AtlasOnBg,
                                focusedBorderColor   = AtlasPrimary,
                                unfocusedBorderColor = AtlasMuted.copy(alpha = 0.4f),
                                cursorColor          = AtlasPrimary,
                            ),
                            modifier = Modifier.fillMaxWidth(),
                        )
                        Spacer(Modifier.height(12.dp))
                        OutlinedTextField(
                            value         = password,
                            onValueChange = { password = it },
                            label         = { Text("LAN password") },
                            placeholder   = { Text("Leave blank if open", color = AtlasMuted) },
                            singleLine    = true,
                            visualTransformation = if (showPassword) VisualTransformation.None else PasswordVisualTransformation(),
                            keyboardOptions = KeyboardOptions(
                                keyboardType = KeyboardType.Password,
                                imeAction    = ImeAction.Done,
                            ),
                            trailingIcon = {
                                IconButton(onClick = { showPassword = !showPassword }) {
                                    Icon(
                                        if (showPassword) Icons.Default.VisibilityOff else Icons.Default.Visibility,
                                        contentDescription = null,
                                        tint = AtlasMuted,
                                    )
                                }
                            },
                            colors = OutlinedTextFieldDefaults.colors(
                                focusedTextColor     = AtlasOnBg,
                                unfocusedTextColor   = AtlasOnBg,
                                focusedBorderColor   = AtlasPrimary,
                                unfocusedBorderColor = AtlasMuted.copy(alpha = 0.4f),
                                cursorColor          = AtlasPrimary,
                            ),
                            modifier = Modifier.fillMaxWidth(),
                        )
                        Spacer(Modifier.height(12.dp))
                        Text(
                            "Atlas remains paired to the device you already found on the hotspot. This step only changes which WiFi network that same Atlas uses.",
                            color    = AtlasMuted,
                            fontSize = 12.sp,
                        )
                    }
                }

                Spacer(Modifier.height(20.dp))

                PrimaryButton(
                    text    = "Connect",
                    enabled = ssid.isNotBlank(),
                    icon    = Icons.Default.Wifi,
                    onClick = {
                        setupVm.connectToLan(ssid.trim(), password)
                    },
                )

                Spacer(Modifier.height(12.dp))

                TextButton(
                    onClick  = { setupVm.completeSetup(appVm) },
                    modifier = Modifier.align(Alignment.CenterHorizontally),
                ) {
                    Text("Skip for now", color = AtlasMuted, fontSize = 14.sp)
                }
            }
        }

        Spacer(Modifier.height(32.dp))
    }
}

private fun isHotspotUrl(url: String): Boolean =
    url.contains("10.42.0.")   ||
    url.contains("192.168.4.") ||
    url.contains("192.168.43.")
