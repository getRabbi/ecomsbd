package com.ecomsbd.app.intake

import com.ecomsbd.app.intake.ConversationBuffer.Incoming
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ConversationBufferTest {
    private val window = 25 * 60_000L
    private val start = 1_800_000_000_000L
    private var ids = 0
    private fun buffer(max: Int = ConversationBuffer.MAX_ENTRIES) =
        ConversationBuffer(maxEntries = max, newId = { "c${++ids}" })

    @Test
    fun messagesFromOneChatJoinAndRepostsAreSkipped() {
        val buffer = buffer()
        buffer.add("com.whatsapp.w4b", "Rina", "Rina", listOf(Incoming("2ta nibo", start)), start, window)
        val second = buffer.add(
            "com.whatsapp.w4b",
            "Rina",
            "Rina",
            listOf(Incoming("2ta nibo", start), Incoming("01700000020", start + 1_000)),
            start + 1_000,
            window,
        )
        assertEquals(1, second.added)
        assertEquals("2ta nibo\n01700000020", second.conversation.text)
        assertEquals(1, buffer.all().size)
        // Without a time from the app, the same text is the same message.
        val third = buffer.add("com.whatsapp.w4b", "Rina", null, listOf(Incoming("01700000020", null)), start + 2_000, window)
        assertEquals(0, third.added)
        assertEquals("Rina", third.conversation.senderDisplay)
    }

    @Test
    fun theSameTextAtAnotherTimeIsANewMessage() {
        val buffer = buffer()
        buffer.add("p.app", "A", null, listOf(Incoming("ok", start)), start, window)
        assertEquals(1, buffer.add("p.app", "A", null, listOf(Incoming("ok", start + 5_000)), start + 5_000, window).added)
    }

    @Test
    fun aQuietChatClosesAfterTheWindow() {
        val buffer = buffer()
        val first = buffer.add("p.app", "A", null, listOf(Incoming("hi", start)), start, window).conversation
        val later = start + window + 60_000
        val next = buffer.add("p.app", "A", null, listOf(Incoming("again", later)), later, window)
        assertEquals(listOf(first.id), next.dropped.map { it.id })
        assertNotEquals(first.id, next.conversation.id)
        assertEquals("again", next.conversation.text)
        assertEquals(first.notificationId, next.conversation.notificationId)
        assertEquals(1, buffer.prune(later + window + 1, window).size)
        assertTrue(buffer.all().isEmpty())
    }

    @Test
    fun aRepostedThreadKeepsOnlyItsOwnWindow() {
        val buffer = buffer()
        val twoDaysAgo = start - 2 * 86_400_000L
        val result = buffer.add(
            "p.app",
            "A",
            null,
            listOf(Incoming("old chat", twoDaysAgo), Incoming("new order", start)),
            // The phone clock is an hour off: only the thread's own times are compared.
            start + 3_600_000,
            window,
        )
        assertEquals("new order", result.conversation.text)
    }

    @Test
    fun atMostMaxEntriesAndTheQuietestChatGoesFirst() {
        val buffer = buffer(max = 4)
        buffer.add("p.app", "A", null, listOf(Incoming("a1", 1), Incoming("a2", 2)), start, window)
        buffer.add("p.app", "B", null, listOf(Incoming("b1", 1), Incoming("b2", 2)), start + 1, window)
        // A grows to 3 messages (5 in all) and becomes the newest: B, quiet longest, goes.
        val grown = buffer.add("p.app", "A", null, listOf(Incoming("a3", 3)), start + 2, window)
        assertEquals(listOf("B"), grown.dropped.map { it.title })
        assertEquals(3, buffer.size)
        val next = buffer.add("p.app", "C", null, listOf(Incoming("c1", 1)), start + 3, window)
        assertTrue(next.dropped.isEmpty())
        assertEquals(listOf("A", "C"), buffer.all().map { it.title })
        assertEquals(4, buffer.size)
    }

    @Test
    fun aChatKeepsItsNewestFiftyMessagesAndLongTextIsCut() {
        val buffer = buffer()
        val many = (1..60).map { Incoming("m$it", start + it) }
        val conversation = buffer.add("p.app", "A", null, many, start + 60, window).conversation
        assertEquals(ConversationBuffer.MAX_MESSAGES, conversation.messages.size)
        assertEquals("m11", conversation.messages.first().text)
        val long = buffer.add("p.app", "B", null, listOf(Incoming("x".repeat(5_000), start)), start, window)
        assertEquals(ConversationBuffer.MAX_TEXT, long.conversation.text.length)
    }

    @Test
    fun blankTextAddsNothingAndLeavesNoEmptyChat() {
        val buffer = buffer()
        assertEquals(0, buffer.add("p.app", "A", null, listOf(Incoming("   ", start)), start, window).added)
        assertNull(buffer.find("p.app", "A"))
    }

    @Test
    fun survivesAJsonRoundTripAndAnUnreadableStoreStartsEmpty() {
        val buffer = buffer()
        val conversation = buffer.add("p.app", "A", "Rina", listOf(Incoming("2ta nibo", start), Incoming("01700000021", null)), start, window).conversation
        conversation.ignored = true
        conversation.uploadedCount = 1
        val restored = ConversationBuffer.fromJson(buffer.toJson())
        val copy = restored.get(conversation.id)!!
        assertEquals(conversation.text, copy.text)
        assertEquals("Rina", copy.senderDisplay)
        assertTrue(copy.ignored)
        assertEquals(1, copy.uploadedCount)
        assertNull(copy.messages[1].sentAt)
        assertTrue(ConversationBuffer.fromJson("{not json").all().isEmpty())
        assertTrue(ConversationBuffer.fromJson(null).all().isEmpty())
    }

    @Test
    fun theHeadsUpIdFollowsTheChat() {
        assertEquals(
            ConversationBuffer.notificationId("p.app|A"),
            ConversationBuffer.notificationId("p.app|A"),
        )
        assertNotEquals(
            ConversationBuffer.notificationId("p.app|A"),
            ConversationBuffer.notificationId("p.app|B"),
        )
    }
}
