package com.ecomsbd.app.intake

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** The Android share target's reading of an incoming intent (V4 WP1.5). */
class SharedTextTest {
    private val send = "android.intent.action.SEND"

    @Test
    fun readsSharedChatText() {
        assertEquals(
            "2ta panjabi, 01700000050, Mirpur",
            SharedText.read(send, "text/plain", "  2ta panjabi, 01700000050, Mirpur \n"),
        )
    }

    @Test
    fun acceptsAnyTextTypeAndStyledText() {
        val styled = StringBuilder("XL ekta")
        assertEquals("XL ekta", SharedText.read(send, "text/html", styled))
    }

    @Test
    fun ignoresOtherActionsTypesAndBlankText() {
        assertNull(SharedText.read("android.intent.action.VIEW", "text/plain", "hello"))
        assertNull(SharedText.read(send, "image/png", "hello"))
        assertNull(SharedText.read(send, null, "hello"))
        assertNull(SharedText.read(send, "text/plain", "   "))
        assertNull(SharedText.read(send, "text/plain", null))
    }

    @Test
    fun capsTextAtTheServersMessageLimit() {
        val long = "a".repeat(SharedText.MAX_CHARS + 50)
        assertEquals(SharedText.MAX_CHARS, SharedText.read(send, "text/plain", long)!!.length)
    }
}
