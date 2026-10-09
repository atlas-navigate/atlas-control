package com.atlascontrol.mobile.ui

import android.Manifest
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import android.widget.Toast
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material.icons.filled.Wifi
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.atlascontrol.mobile.*

/** Copy to the clipboard and say so (Android 13+ shows its own confirmation). */
fun copyToClipboard(context: Context, label: String, text: String) {
    val cm = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
    cm.setPrimaryClip(ClipData.newPlainText(label, text))
    if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) {
        Toast.makeText(context, "Copied", Toast.LENGTH_SHORT).show()
    }
}

/** Shown once, right after pairing: this Atlas's own address on the local network. */
@Composable
fun YourAtlasAddress(name: String, hostname: String, onContinue: () -> Unit) {
    val context = LocalContext.current
    Column(
        Modifier.fillMaxSize().background(AtlasBackground).statusBarsPadding().padding(28.dp),
        verticalArrangement = Arrangement.Center, horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text("$name is connected", fontSize = 22.sp, fontWeight = FontWeight.Bold, color = AtlasOnBg,
            textAlign = TextAlign.Center)
        Spacer(Modifier.height(12.dp))
        Text("This app finds Atlas by itself from now on. On the same Wi-Fi, any phone or computer can also " +
            "open it at its own address:", color = AtlasOnSurface, fontSize = 15.sp, textAlign = TextAlign.Center)
        Spacer(Modifier.height(18.dp))
        Card(colors = CardDefaults.cardColors(containerColor = AtlasSurface2), shape = RoundedCornerShape(14.dp)) {
            Row(Modifier.padding(horizontal = 18.dp, vertical = 14.dp), verticalAlignment = Alignment.CenterVertically) {
                Text(hostname, fontSize = 20.sp, fontWeight = FontWeight.Bold, color = AtlasPrimary,
                    fontFamily = FontFamily.Monospace, modifier = Modifier.weight(1f, fill = false))
                Spacer(Modifier.width(10.dp))
                IconButton(onClick = { copyToClipboard(context, "Atlas address", hostname) }) {
                    Icon(Icons.Default.ContentCopy, contentDescription = "Copy address", tint = AtlasPrimary)
                }
            }
        }
        Spacer(Modifier.height(10.dp))
        Text("No need to remember an IP address, which your router may change. It's also in Atlas's " +
            "Settings → Local Device Address.", color = AtlasMuted, fontSize = 13.sp, textAlign = TextAlign.Center)
        Spacer(Modifier.height(26.dp))
        Button(onClick = onContinue, modifier = Modifier.fillMaxWidth().height(50.dp), shape = RoundedCornerShape(12.dp)) {
            Text("Open Atlas", fontWeight = FontWeight.Bold)
        }
    }
}

/** The paired Atlas's address details while it can't be reached. */
@Composable
fun DeviceDetailsCard(name: String, hostname: String?, lastAddress: String?, connected: Boolean = false) {
    val context = LocalContext.current
    Card(Modifier.fillMaxWidth(), colors = CardDefaults.cardColors(containerColor = AtlasSurface2),
        shape = RoundedCornerShape(12.dp)) {
        Column(Modifier.padding(16.dp)) {
            Text("Local device address", color = AtlasMuted, fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.height(8.dp))
            DetailRow("Device name", name)
            if (hostname != null) {
                DetailRow("Local hostname", hostname, onCopy = { copyToClipboard(context, "Atlas address", hostname) })
            }
            DetailRow("Last IP address", lastAddress ?: "—")
            Row(Modifier.padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                Text("Status", color = AtlasMuted, fontSize = 13.sp, modifier = Modifier.width(120.dp))
                Box(Modifier.size(9.dp).background(if (connected) AtlasPrimary else AtlasError, CircleShape))
                Spacer(Modifier.width(6.dp))
                Text(if (connected) "Connected" else "Disconnected", color = AtlasOnBg, fontSize = 14.sp)
            }
        }
    }
}

@Composable
private fun DetailRow(label: String, value: String, onCopy: (() -> Unit)? = null) {
    Row(Modifier.padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(label, color = AtlasMuted, fontSize = 13.sp, modifier = Modifier.width(120.dp))
        Text(value, color = AtlasOnBg, fontSize = 14.sp, fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
        if (onCopy != null) {
            IconButton(onClick = onCopy, modifier = Modifier.size(32.dp)) {
                Icon(Icons.Default.ContentCopy, contentDescription = "Copy $label", tint = AtlasPrimary,
                    modifier = Modifier.size(18.dp))
            }
        }
    }
}

/**
 * Android refused this app access to the local network. Explain, then ask
 * (Android 13+ grants local-network access with "Nearby devices"), or send
 * the owner to the app's settings if it was turned off there.
 */
@Composable
fun LocalNetworkPermissionCard(onRetry: () -> Unit) {
    val context = LocalContext.current
    val ask = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) onRetry() else openAppSettings(context)
    }
    Card(Modifier.fillMaxWidth(), colors = CardDefaults.cardColors(containerColor = AtlasSurface2),
        shape = RoundedCornerShape(12.dp)) {
        Column(Modifier.padding(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.Default.Wifi, null, tint = AtlasWarning)
                Spacer(Modifier.width(10.dp))
                Text("Allow access to your Wi-Fi network", color = AtlasOnBg, fontWeight = FontWeight.Bold)
            }
            Spacer(Modifier.height(8.dp))
            Text("Atlas lives on your local network, and Android is blocking this app from looking there. " +
                "Allow it so the app can find and connect to your Atlas. Nothing leaves your network.",
                color = AtlasOnSurface, fontSize = 14.sp)
            Spacer(Modifier.height(10.dp))
            Button(onClick = {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) ask.launch(Manifest.permission.NEARBY_WIFI_DEVICES)
                else openAppSettings(context)
            }, modifier = Modifier.fillMaxWidth()) { Text("Allow") }
        }
    }
}

private fun openAppSettings(context: Context) {
    runCatching {
        context.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
            Uri.fromParts("package", context.packageName, null)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }
}
