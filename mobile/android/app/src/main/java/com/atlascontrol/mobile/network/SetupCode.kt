package com.atlascontrol.mobile.network

/**
 * The setup code on an Atlas label: three groups of four capitals and
 * digits (H7QD-M3XR-9KTW). The Wi-Fi password on the same label is four
 * lowercase groups, so the two can't be mixed up. Atlas ignores dashes and
 * case; formatting here only helps the owner type it.
 */
object SetupCode {
    const val LENGTH = 12

    /** Uppercase letters and digits only, grouped in fours as the owner types. */
    fun format(input: String): String =
        input.uppercase().filter { it in 'A'..'Z' || it in '0'..'9' }.take(LENGTH).chunked(4).joinToString("-")

    fun isComplete(input: String): Boolean = format(input).count { it != '-' } == LENGTH

    /** Four lowercase groups: the label's Wi-Fi password, typed in the wrong place. */
    fun looksLikeWifiPassword(input: String): Boolean =
        Regex("^[a-z0-9]{4}(-[a-z0-9]{4}){3}$").matches(input.trim())
}
