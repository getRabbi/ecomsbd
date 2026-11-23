package com.ecomsbd.app.intake

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

/** Loads the real v1 body (`GET /v1/intake/rules`, generated from the backend seed). */
internal fun seedRules(): IntakeRules {
    val stream = checkNotNull(IntakeClassifierTest::class.java.classLoader)
        .getResourceAsStream("intake/rules_v1.json")
    return IntakeRules.parse(checkNotNull(stream).bufferedReader(Charsets.UTF_8).readText())
}

internal fun codePoints(vararg points: Int): String = String(points, 0, points.size)

class IntakeClassifierTest {
    private val rules = seedRules()
    private val classifier = IntakeClassifier(rules)

    @Test
    fun parsesTheSeedRules() {
        assertEquals(1, rules.version)
        assertEquals(setOf("com.facebook.pages.app", "com.whatsapp.w4b"), rules.enabledPackages)
        assertEquals(4, rules.packages.size)
        assertEquals(25 * 60_000L, rules.bufferWindowMillis)
        assertEquals(24 * 3_600_000L, rules.refreshAfterMillis)
        assertEquals(60, rules.score.threshold)
        assertEquals(64, rules.districts.size)
    }

    @Test
    fun banglishOrderWithPhoneAndDistrictIsOrderLike() {
        val result = classifier.classify("Assalamualaikum, 2ta nibo. 01700000001, Mirpur, Dhaka")
        assertEquals(listOf("01700000001"), result.phones)
        assertEquals(1, result.intentHits)
        assertEquals("Dhaka", result.district)
        assertEquals(40 + 20 + 20, result.score)
        assertTrue(result.orderLike)
        assertEquals("01700•••01", result.maskedPhone)
    }

    @Test
    fun aPhoneAloneIsNotAnOrder() {
        val result = classifier.classify("amar number 01700000002")
        assertEquals(40, result.score)
        assertFalse(result.orderLike)
    }

    @Test
    fun noPhoneIsNeverOrderLikeHoweverHighTheScore() {
        val result = classifier.classify("order korbo, home delivery, address: Dhaka")
        assertTrue(result.phones.isEmpty())
        assertTrue(result.score >= rules.score.threshold)
        assertFalse(result.orderLike)
    }

    @Test
    fun banglaDigitsAndCountryPrefixAreNationalNumbers() {
        val result = classifier.classify("অর্ডার করতে চাই। +৮৮০১৭০০০০০০০৩ ঠিকানা: মিরপুর, ঢাকা")
        assertEquals(listOf("01700000003"), result.phones)
        assertEquals(1, result.intentHits)
        assertEquals(1, result.addressHits)
        assertEquals("Dhaka", result.district)
        assertEquals(90, result.score)
        assertTrue(result.orderLike)
    }

    @Test
    fun separatorsInsideANumberAreAllowed() {
        assertEquals(listOf("01700000005"), classifier.classify("call 0170-0000-005 please").phones)
        assertEquals(listOf("01700000005"), classifier.classify("88 01700 000005").phones)
    }

    @Test
    fun aNumberInsideALongerDigitRunIsNotAPhone() {
        assertTrue(classifier.classify("Order ID 9901700000004123, delivery Dhaka").phones.isEmpty())
    }

    @Test
    fun distinctPhonesInOrderOfAppearance() {
        val result = classifier.classify("01700000011 na hole 01700000012, abar 01700000011")
        assertEquals(listOf("01700000011", "01700000012"), result.phones)
    }

    @Test
    fun latinKeywordsMatchWholeWordsWithDigitsAsBoundaries() {
        val counted = classifier.classify("2pcs lagbe 01700000006 Dhaka")
        assertEquals(2, counted.intentHits) // "pcs" and "lagbe"; "pc" inside "pcs" is not a word
        assertEquals(100, counted.score)
        assertEquals(0, classifier.classify("recorder 01700000007").intentHits)
    }

    @Test
    fun overlappingKeywordsCountOnceAndRepeatsCountOnce() {
        assertEquals(1, classifier.classify("cash on delivery").intentHits)
        assertEquals(1, classifier.classify("order order order").intentHits)
    }

    @Test
    fun banglaKeywordsMatchWithSuffixes() {
        assertEquals(2, classifier.classify("অর্ডারটা কনফার্ম করেন").intentHits)
    }

    @Test
    fun banglaMatchesTheSameWhetherNuktaIsPrecomposedOrNot() {
        // বাড়ি written with U+09DC and with U+09A1 U+09BC: both are the address keyword.
        val precomposed = codePoints(0x09AC, 0x09BE, 0x09DC, 0x09BF)
        val decomposed = codePoints(0x09AC, 0x09BE, 0x09A1, 0x09BC, 0x09BF)
        assertEquals(1, classifier.classify("$precomposed 12").addressHits)
        assertEquals(1, classifier.classify("$decomposed 12").addressHits)
    }

    @Test
    fun theEarliestDistrictIsTheAreaGuessAndLongerSpellingsWin() {
        assertEquals("Chattogram", classifier.classify("Chattogram theke, pathan Dhaka").district)
        val cox = classifier.classify("Cox's Bazar e pathiye din 01700000008")
        assertEquals("Cox's Bazar", cox.district)
        assertTrue(cox.orderLike)
        assertNull(classifier.classify("01700000009 nibo").district)
    }

    @Test
    fun masksKeepFivePlusTwoDigits() {
        assertEquals("01700•••10", IntakeClassifier.maskPhone("01700000010"))
        assertEquals("•••", IntakeClassifier.maskPhone("123"))
    }

    @Test
    fun anUnknownSchemaOrABrokenBodyIsRefusedWithoutEchoingIt() {
        assertThrows(IntakeRules.UnsupportedRulesException::class.java) {
            IntakeRules.parse("""{"schema_version": 2, "version": 9}""")
        }
        val broken = assertThrows(IntakeRules.UnsupportedRulesException::class.java) {
            IntakeRules.parse("""{"schema_version": 1, "secret": "01700000013"}""")
        }
        assertFalse(broken.message.orEmpty().contains("01700000013"))
    }
}
