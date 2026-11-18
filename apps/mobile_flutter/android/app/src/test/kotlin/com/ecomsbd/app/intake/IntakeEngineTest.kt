package com.ecomsbd.app.intake

import com.ecomsbd.app.intake.ConversationBuffer.Incoming
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class IntakeEngineTest {
    private val rules = seedRules()
    private val buffer = ConversationBuffer()
    private val secret = ByteArray(32) { it.toByte() }
    private val blockList = SenderBlockList(secret)
    private val engine = IntakeEngine(rules, buffer, blockList)
    private val now = 1_800_000_000_000L
    private val app = "com.whatsapp.w4b"

    private fun post(title: String, text: String, at: Long, autopilot: Boolean = false) =
        engine.onNotification(
            CapturedNotification(app, title, title, listOf(Incoming(text, at))),
            now = at,
            autopilotOn = autopilot,
        )

    @Test
    fun appsTheShopDidNotTurnOnAreNotRead() {
        val outcome = engine.onNotification(
            CapturedNotification("com.whatsapp", "Rina", null, listOf(Incoming("01700000030 nibo Dhaka", now))),
            now,
            autopilotOn = false,
        )
        assertEquals(IntakeEngine.Skip.PACKAGE_NOT_ENABLED, (outcome as IntakeEngine.Skipped).reason)
        assertTrue(buffer.all().isEmpty())
    }

    @Test
    fun anOrderTypedOverSeveralMessagesRaisesOneHeadsUp() {
        val first = post("Rina", "apu kemon acen", now) as IntakeEngine.Buffered
        assertFalse(first.notify) // no phone yet
        val second = post("Rina", "01700000031", now + 1_000) as IntakeEngine.Buffered
        assertFalse(second.notify) // a phone alone (40) is below 60
        val third = post("Rina", "2ta nibo, Mirpur 10, Dhaka", now + 2_000) as IntakeEngine.Buffered
        assertTrue(third.notify)
        assertEquals(80, third.result.score)
        // A fourth message updates the same heads-up in place.
        val fourth = post("Rina", "cash on delivery", now + 3_000) as IntakeEngine.Buffered
        assertTrue(fourth.notify)
        assertEquals(first.conversation.id, fourth.conversation.id)
        assertEquals(first.conversation.notificationId, fourth.conversation.notificationId)
        assertEquals("Dhaka", fourth.result.district)
        assertNull(fourth.upload) // no Autopilot: nothing leaves the phone without a tap
    }

    @Test
    fun aRepostWithNothingNewChangesNothing() {
        post("Rina", "2ta nibo 01700000032 Dhaka", now)
        val again = post("Rina", "2ta nibo 01700000032 Dhaka", now)
        assertEquals(IntakeEngine.Skip.NOTHING_NEW, (again as IntakeEngine.Skipped).reason)
    }

    @Test
    fun ignoreKeepsReadingButRaisesNoHeadsUp() {
        val shown = post("Rina", "2ta nibo 01700000033 Dhaka", now) as IntakeEngine.Buffered
        assertTrue(shown.notify)
        engine.ignore(shown.conversation.id)
        val after = post("Rina", "address: Mirpur 10", now + 1_000) as IntakeEngine.Buffered
        assertFalse(after.notify)
        assertTrue(after.result.orderLike)
    }

    @Test
    fun aBlockedSenderIsForgottenAndNotNamedOnDisk() {
        val shown = post("Rina Akter", "2ta nibo 01700000034 Dhaka", now) as IntakeEngine.Buffered
        engine.block(shown.conversation.id)
        assertNull(buffer.get(shown.conversation.id))
        val outcome = post("Rina Akter", "aro 1ta nibo 01700000034 Dhaka", now + 1_000)
        assertEquals(IntakeEngine.Skip.SENDER_BLOCKED, (outcome as IntakeEngine.Skipped).reason)
        assertFalse(blockList.toJson().contains("Rina"))
        assertTrue(SenderBlockList.fromJson(secret, blockList.toJson()).isBlocked(app, "Rina Akter"))
        assertFalse(SenderBlockList.fromJson(ByteArray(32), blockList.toJson()).isBlocked(app, "Rina Akter"))
    }

    @Test
    fun autopilotUploadsOnlyOrderLikeChatsAndOnlyNewMessages() {
        val notYet = post("Rina", "apu 2ta nibo", now, autopilot = true) as IntakeEngine.Buffered
        assertNull(notYet.upload)
        val first = post("Rina", "01700000035 Dhaka", now + 1_000, autopilot = true) as IntakeEngine.Buffered
        assertEquals(listOf("apu 2ta nibo", "01700000035 Dhaka"), first.upload!!.map { it.text })
        val next = post("Rina", "Mirpur 10", now + 2_000, autopilot = true) as IntakeEngine.Buffered
        assertEquals(listOf("Mirpur 10"), next.upload!!.map { it.text })
    }
}
