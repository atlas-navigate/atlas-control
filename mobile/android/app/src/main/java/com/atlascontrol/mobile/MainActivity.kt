package com.atlascontrol.mobile

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.GppMaybe
import androidx.compose.material.icons.filled.WifiOff
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import com.atlascontrol.mobile.notifications.AtlasNotificationManager
import com.atlascontrol.mobile.ui.ConnectionViewModel
import com.atlascontrol.mobile.ui.OnboardingScreen
import com.atlascontrol.mobile.ui.Screen
import com.atlascontrol.mobile.ui.web.AtlasWebScreen

class MainActivity : ComponentActivity() {
    private val vm by viewModels<ConnectionViewModel>()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        AtlasNotificationManager.ensureChannels(this)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1001)
        }
        enableEdgeToEdge()
        setContent { AtlasTheme { AtlasApp(vm) } }
    }
}

@Composable
private fun AtlasApp(vm: ConnectionViewModel) {
    val screen by vm.screen.collectAsState()
    val snackbar = remember { SnackbarHostState() }
    LaunchedEffect(Unit) { vm.events.collect { snackbar.showSnackbar(it) } }

    Scaffold(snackbarHost = { SnackbarHost(snackbar) }, containerColor = AtlasBackground) { pad ->
        Box(Modifier.padding(pad)) {
            AnimatedContent(targetState = screen, transitionSpec = { fadeIn() togetherWith fadeOut() },
                contentKey = { it::class }, label = "atlas_root") { s ->
                when (s) {
                    Screen.Onboarding -> OnboardingScreen(vm)
                    is Screen.Locating -> Waiting(s.message)
                    is Screen.Restarting -> Waiting(s.message, detail = "Atlas reconnects by itself — keep this app open.")
                    is Screen.Connected -> AtlasWebScreen(vm, s.baseUrl)
                    Screen.NotFound -> Problem(
                        icon = Icons.Default.WifiOff,
                        title = "Can't find ${vm.deviceName}",
                        body = "Make sure Atlas is switched on and this phone is on the same Wi-Fi, " +
                            "or on Atlas's own Wi-Fi (its name and password are on the label). " +
                            "The app keeps looking and connects as soon as Atlas is reachable.",
                        primary = "Try again" to { vm.relocate() },
                        secondary = null,
                    )
                    Screen.SecurityWarning -> Problem(
                        icon = Icons.Default.GppMaybe,
                        title = "Security warning",
                        body = "${vm.deviceName} is answering with a different security key than when you connected it. " +
                            "If this Atlas was reset or replaced, connect it again. Otherwise, something on this network " +
                            "may be pretending to be your Atlas — don't continue.",
                        primary = "Connect again" to { vm.forgetDevice() },
                        secondary = "Try again" to { vm.relocate() },
                    )
                }
            }
        }
    }
}

@Composable
private fun Waiting(message: String, detail: String? = null) {
    Column(Modifier.fillMaxSize().background(AtlasBackground).padding(32.dp),
        verticalArrangement = Arrangement.Center, horizontalAlignment = Alignment.CenterHorizontally) {
        CircularProgressIndicator(color = AtlasPrimary)
        Spacer(Modifier.height(20.dp))
        Text(message, color = AtlasOnBg, fontSize = 17.sp, textAlign = TextAlign.Center)
        if (detail != null) {
            Spacer(Modifier.height(8.dp))
            Text(detail, color = AtlasMuted, fontSize = 13.sp, textAlign = TextAlign.Center)
        }
    }
}

@Composable
private fun Problem(icon: ImageVector, title: String, body: String,
                    primary: Pair<String, () -> Unit>, secondary: Pair<String, () -> Unit>?) {
    Column(Modifier.fillMaxSize().background(AtlasBackground).padding(32.dp),
        verticalArrangement = Arrangement.Center, horizontalAlignment = Alignment.CenterHorizontally) {
        Icon(icon, null, tint = AtlasWarning, modifier = Modifier.size(52.dp))
        Spacer(Modifier.height(16.dp))
        Text(title, color = AtlasOnBg, fontSize = 20.sp, fontWeight = FontWeight.Bold, textAlign = TextAlign.Center)
        Spacer(Modifier.height(10.dp))
        Text(body, color = AtlasOnSurface, fontSize = 14.sp, textAlign = TextAlign.Center)
        Spacer(Modifier.height(24.dp))
        Button(onClick = primary.second, modifier = Modifier.fillMaxWidth().height(48.dp), shape = RoundedCornerShape(10.dp)) {
            Text(primary.first, fontWeight = FontWeight.Bold)
        }
        if (secondary != null) {
            TextButton(onClick = secondary.second) { Text(secondary.first) }
        }
    }
}
