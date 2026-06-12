package com.atlascontrol.mobile

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

val AtlasBackground  = Color(0xFF09111C)
val AtlasSurface     = Color(0xFF162438)
val AtlasSurface2    = Color(0xFF1E3248)
val AtlasPrimary     = Color(0xFF5AA7FF)
val AtlasSecondary   = Color(0xFF5EF2C2)
val AtlasTertiary    = Color(0xFFFFBE5C)
val AtlasOnBg        = Color(0xFFF3F7FB)
val AtlasOnSurface   = Color(0xFFCBD9EC)
val AtlasMuted       = Color(0xFF5A7A9E)
val AtlasError       = Color(0xFFFF5C72)
val AtlasWarning     = Color(0xFFFFBE5C)
val AtlasSuccess     = Color(0xFF5EF2C2)

private val AtlasColorScheme = darkColorScheme(
    primary            = AtlasPrimary,
    onPrimary          = AtlasBackground,
    primaryContainer   = Color(0xFF1A3A60),
    onPrimaryContainer = AtlasPrimary,
    secondary          = AtlasSecondary,
    onSecondary        = AtlasBackground,
    tertiary           = AtlasTertiary,
    onTertiary         = AtlasBackground,
    background         = AtlasBackground,
    onBackground       = AtlasOnBg,
    surface            = AtlasSurface,
    onSurface          = AtlasOnSurface,
    surfaceVariant     = AtlasSurface2,
    onSurfaceVariant   = AtlasMuted,
    error              = AtlasError,
    onError            = Color.White,
    outline            = AtlasMuted,
)

@Composable
fun AtlasTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = AtlasColorScheme,
        content     = content,
    )
}
