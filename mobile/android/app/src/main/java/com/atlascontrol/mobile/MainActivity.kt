package com.atlascontrol.mobile

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.compose.animation.*
import androidx.compose.animation.core.*
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Hub
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.WifiOff
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.atlascontrol.mobile.notifications.AtlasNotificationManager
import com.atlascontrol.mobile.setup.SetupViewModel
import com.atlascontrol.mobile.setup.SetupWizardScreen
import com.atlascontrol.mobile.ui.AppViewModel
import com.atlascontrol.mobile.ui.ConnectionState
import com.atlascontrol.mobile.ui.web.AtlasWebScreen
import kotlinx.coroutines.delay

class MainActivity : ComponentActivity() {
    companion object {
        private const val NOTIFICATION_PERMISSION_REQUEST = 1001
    }

    private val appVm   by viewModels<AppViewModel>()
    private val setupVm by viewModels<SetupViewModel>()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        AtlasNotificationManager.ensureChannels(this)
        requestNotificationPermissionIfNeeded()
        enableEdgeToEdge()
        setContent {
            AtlasTheme {
                AtlasApp(appVm, setupVm)
            }
        }
    }

    private fun requestNotificationPermissionIfNeeded() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED) return
        ActivityCompat.requestPermissions(
            this,
            arrayOf(Manifest.permission.POST_NOTIFICATIONS),
            NOTIFICATION_PERMISSION_REQUEST,
        )
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<String>,
        grantResults: IntArray,
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == NOTIFICATION_PERMISSION_REQUEST &&
            grantResults.firstOrNull() == PackageManager.PERMISSION_DENIED) {
            // The user denied the notification permission — open the app's notification
            // settings page so they can enable it without hunting through system menus.
            runCatching {
                startActivity(
                    Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).apply {
                        putExtra(Settings.EXTRA_APP_PACKAGE, packageName)
                    }
                )
            }
        }
    }
}

@Composable
private fun AtlasApp(appVm: AppViewModel, setupVm: SetupViewModel) {
    val state by appVm.state.collectAsState()

    AnimatedContent(
        targetState = state,
        transitionSpec = { fadeIn(animationSpec = androidx.compose.animation.core.tween(300)) togetherWith fadeOut(animationSpec = androidx.compose.animation.core.tween(200)) },
        label = "atlas_root",
    ) { s ->
        when (s) {
            ConnectionState.IDLE      -> SetupWizardScreen(setupVm, appVm)
            ConnectionState.CHECKING  -> ConnectingScreen(appVm)
            ConnectionState.CONNECTED -> AtlasWebScreen(appVm, setupVm)
            ConnectionState.FAILED    -> ErrorScreen(appVm)
        }
    }
}

// ─── Connecting / probe-in-progress ──────────────────────────────────────────

@Composable
private fun ConnectingScreen(appVm: AppViewModel) {
    val isLanTransitioning by appVm.isLanTransitioning.collectAsState()

    // Count how many seconds we've been on the connecting screen.
    var elapsedSeconds by remember { mutableIntStateOf(0) }
    LaunchedEffect(isLanTransitioning) {
        elapsedSeconds = 0
        while (true) {
            delay(1_000L)
            elapsedSeconds++
        }
    }

    // Manual IP entry state — shown after 20 s during a LAN transition.
    var manualIp by remember { mutableStateOf("") }
    val showManualEntry = isLanTransitioning && elapsedSeconds >= 20

    val infiniteTransition = rememberInfiniteTransition(label = "glow")
    val glowAlpha by infiniteTransition.animateFloat(
        initialValue  = 0.15f,
        targetValue   = 0.55f,
        animationSpec = infiniteRepeatable(
            animation  = tween(1400, easing = FastOutSlowInEasing),
            repeatMode = RepeatMode.Reverse,
        ),
        label = "glow_alpha",
    )
    val ringScale by infiniteTransition.animateFloat(
        initialValue  = 0.85f,
        targetValue   = 1.15f,
        animationSpec = infiniteRepeatable(
            animation  = tween(1400, easing = FastOutSlowInEasing),
            repeatMode = RepeatMode.Reverse,
        ),
        label = "ring_scale",
    )

    Box(
        Modifier
            .fillMaxSize()
            .background(
                Brush.radialGradient(
                    listOf(Color(0xFF0F1F38), AtlasBackground),
                    radius = 1400f,
                )
            )
            .safeDrawingPadding(),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            modifier = Modifier.padding(horizontal = 32.dp),
        ) {
            // Icon with pulsing glow ring
            Box(
                contentAlignment = Alignment.Center,
                modifier = Modifier.size(130.dp),
            ) {
                Box(
                    Modifier
                        .size((130 * ringScale).dp)
                        .background(
                            Brush.radialGradient(
                                listOf(AtlasPrimary.copy(alpha = glowAlpha), Color.Transparent)
                            ),
                            CircleShape,
                        )
                )
                Box(
                    Modifier
                        .size(80.dp)
                        .background(
                            Brush.radialGradient(listOf(Color(0xFF1A3A60), Color(0xFF0C1A2E))),
                            CircleShape,
                        )
                        .border(1.dp, AtlasPrimary.copy(alpha = 0.35f), CircleShape),
                    contentAlignment = Alignment.Center,
                ) {
                    Icon(
                        Icons.Default.Hub,
                        null,
                        tint     = AtlasPrimary,
                        modifier = Modifier.size(38.dp),
                    )
                }
            }

            Spacer(Modifier.height(28.dp))
            Text(
                "Atlas Control",
                fontSize   = 28.sp,
                fontWeight = FontWeight.Bold,
                color      = AtlasOnBg,
            )
            Spacer(Modifier.height(6.dp))
            Text(
                if (isLanTransitioning) "Searching for Atlas on LAN…" else "Connecting…",
                color    = AtlasMuted,
                fontSize = 14.sp,
            )
            Spacer(Modifier.height(36.dp))
            CircularProgressIndicator(
                color       = AtlasPrimary,
                strokeWidth = 2.5.dp,
                modifier    = Modifier.size(32.dp),
            )

            // Manual IP entry — shown after 20 s of LAN transition so the user
            // can bypass discovery if auto-scan fails to find Atlas.
            AnimatedVisibility(
                visible = showManualEntry,
                enter   = fadeIn() + expandVertically(),
                exit    = fadeOut() + shrinkVertically(),
            ) {
                Column(
                    horizontalAlignment = Alignment.CenterHorizontally,
                    modifier = Modifier.padding(top = 36.dp),
                ) {
                    HorizontalDivider(
                        color    = AtlasMuted.copy(alpha = 0.3f),
                        modifier = Modifier.padding(bottom = 20.dp),
                    )
                    Text(
                        "Still searching… (${elapsedSeconds}s)",
                        color    = AtlasMuted,
                        fontSize = 12.sp,
                    )
                    Spacer(Modifier.height(12.dp))
                    Text(
                        "Can't find Atlas automatically?",
                        color      = AtlasOnBg,
                        fontSize   = 14.sp,
                        fontWeight = FontWeight.Medium,
                        textAlign  = TextAlign.Center,
                    )
                    Text(
                        "Enter the Atlas IP address or hostname from your LAN.",
                        color     = AtlasMuted,
                        fontSize  = 12.sp,
                        textAlign = TextAlign.Center,
                    )
                    Spacer(Modifier.height(16.dp))
                    OutlinedTextField(
                        value         = manualIp,
                        onValueChange = { manualIp = it },
                        label         = { Text("Atlas IP  (e.g. 192.168.1.50)") },
                        singleLine    = true,
                        modifier      = Modifier.fillMaxWidth(),
                        colors        = OutlinedTextFieldDefaults.colors(
                            focusedBorderColor   = AtlasPrimary,
                            unfocusedBorderColor = AtlasMuted.copy(alpha = 0.5f),
                            focusedLabelColor    = AtlasPrimary,
                            unfocusedLabelColor  = AtlasMuted,
                            focusedTextColor     = AtlasOnBg,
                            unfocusedTextColor   = AtlasOnBg,
                            cursorColor          = AtlasPrimary,
                        ),
                        shape         = RoundedCornerShape(10.dp),
                        keyboardOptions = KeyboardOptions(
                            keyboardType = KeyboardType.Uri,
                            imeAction    = ImeAction.Go,
                        ),
                        keyboardActions = KeyboardActions(
                            onGo = { if (manualIp.isNotBlank()) appVm.connectToManualIp(manualIp) }
                        ),
                    )
                    Spacer(Modifier.height(10.dp))
                    Button(
                        onClick  = { if (manualIp.isNotBlank()) appVm.connectToManualIp(manualIp) },
                        enabled  = manualIp.isNotBlank(),
                        modifier = Modifier.fillMaxWidth().height(48.dp),
                        shape    = RoundedCornerShape(10.dp),
                        colors   = ButtonDefaults.buttonColors(
                            containerColor = AtlasPrimary,
                            contentColor   = AtlasBackground,
                        ),
                    ) {
                        Text("Connect to this IP", fontWeight = FontWeight.Bold)
                    }
                }
            }
        }
    }
}

// ─── Error ────────────────────────────────────────────────────────────────────

@Composable
private fun ErrorScreen(appVm: AppViewModel) {
    val errMsg  by appVm.errorMsg.collectAsState()
    val baseUrl by appVm.baseUrl.collectAsState()

    var manualIp by remember { mutableStateOf("") }

    Box(
        Modifier
            .fillMaxSize()
            .background(AtlasBackground)
            .safeDrawingPadding(),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            Modifier.padding(32.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Icon(Icons.Default.WifiOff, null, tint = AtlasError, modifier = Modifier.size(56.dp))
            Spacer(Modifier.height(16.dp))
            Text(
                "Could not reach Atlas",
                fontSize   = 22.sp,
                fontWeight = FontWeight.Bold,
                color      = AtlasOnBg,
            )
            Spacer(Modifier.height(8.dp))
            Text(
                errMsg ?: baseUrl?.let { "No response from $it" } ?: "Atlas is not reachable.",
                color     = AtlasMuted,
                fontSize  = 14.sp,
                textAlign = TextAlign.Center,
            )
            Spacer(Modifier.height(6.dp))
            Text(
                "Join the atlas_navigate hotspot or ensure your phone is on the same LAN as Atlas.",
                color     = AtlasMuted,
                fontSize  = 12.sp,
                textAlign = TextAlign.Center,
            )
            Spacer(Modifier.height(24.dp))
            Button(
                onClick  = appVm::retry,
                modifier = Modifier.fillMaxWidth().height(52.dp),
                shape    = RoundedCornerShape(10.dp),
                colors   = ButtonDefaults.buttonColors(
                    containerColor = AtlasPrimary,
                    contentColor   = AtlasBackground,
                ),
            ) {
                Icon(Icons.Default.Refresh, null, modifier = Modifier.size(18.dp))
                Spacer(Modifier.width(8.dp))
                Text("Retry", fontWeight = FontWeight.Bold, fontSize = 16.sp)
            }

            // Manual IP entry — lets the user bypass discovery entirely.
            HorizontalDivider(
                color    = AtlasMuted.copy(alpha = 0.3f),
                modifier = Modifier.padding(vertical = 24.dp),
            )
            Text(
                "Or connect manually",
                color      = AtlasOnBg,
                fontSize   = 14.sp,
                fontWeight = FontWeight.Medium,
            )
            Spacer(Modifier.height(10.dp))
            OutlinedTextField(
                value         = manualIp,
                onValueChange = { manualIp = it },
                label         = { Text("Atlas IP  (e.g. 192.168.1.50)") },
                singleLine    = true,
                modifier      = Modifier.fillMaxWidth(),
                colors        = OutlinedTextFieldDefaults.colors(
                    focusedBorderColor   = AtlasPrimary,
                    unfocusedBorderColor = AtlasMuted.copy(alpha = 0.5f),
                    focusedLabelColor    = AtlasPrimary,
                    unfocusedLabelColor  = AtlasMuted,
                    focusedTextColor     = AtlasOnBg,
                    unfocusedTextColor   = AtlasOnBg,
                    cursorColor          = AtlasPrimary,
                ),
                shape           = RoundedCornerShape(10.dp),
                keyboardOptions = KeyboardOptions(
                    keyboardType = KeyboardType.Uri,
                    imeAction    = ImeAction.Go,
                ),
                keyboardActions = KeyboardActions(
                    onGo = { if (manualIp.isNotBlank()) appVm.connectToManualIp(manualIp) }
                ),
            )
            Spacer(Modifier.height(10.dp))
            Button(
                onClick  = { if (manualIp.isNotBlank()) appVm.connectToManualIp(manualIp) },
                enabled  = manualIp.isNotBlank(),
                modifier = Modifier.fillMaxWidth().height(48.dp),
                shape    = RoundedCornerShape(10.dp),
                colors   = ButtonDefaults.buttonColors(
                    containerColor = AtlasSurface2,
                    contentColor   = AtlasOnBg,
                ),
            ) {
                Text("Connect to this IP", fontWeight = FontWeight.Bold)
            }
        }
    }
}
