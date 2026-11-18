package com.ecomsbd.app.intake

import kotlin.math.min

/**
 * Decides on the phone whether a conversation looks like an order (rule L4),
 * using only the server's rules — nothing here is a guess of this build.
 *
 * Score = phone + intent·min(hits, max) + address·min(hits, max) + district.
 * A valid phone number is mandatory, and the rules guarantee a phone alone never
 * reaches the threshold.
 *
 * Matching (the extension must mean the same thing):
 * * text is normalised by [IntakeText.normalize] (NFC, lower-case, one space);
 * * a phone span is any match of a phone pattern; Bangla digits become ASCII,
 *   everything else but digits is dropped, then the national pattern must match
 *   either the digits or the digits without the country prefix;
 * * Bangla keywords match as substrings (Bangla attaches suffixes);
 * * Latin keywords match whole words: the characters around the match must not
 *   be ASCII letters (digits count as a boundary, so "2pcs" and "3rd" match);
 * * overlapping keyword matches count once (the longest wins), and a keyword
 *   counts once however often it appears;
 * * a district counts once; the earliest one in the text is the area guess.
 */
class IntakeClassifier(private val rules: IntakeRules) {

    data class Result(
        val score: Int,
        val orderLike: Boolean,
        /** National numbers (`01XXXXXXXXX`), in order of appearance, distinct. */
        val phones: List<String>,
        val intentHits: Int,
        val addressHits: Int,
        /** Canonical district name, or null. */
        val district: String?,
    ) {
        val maskedPhone: String? get() = phones.firstOrNull()?.let(::maskPhone)
    }

    fun classify(text: String): Result {
        val normalized = IntakeText.normalize(text)
        val phones = findPhones(normalized)
        val intent = countHits(normalized, rules.intentBangla, rules.intentLatin)
        val address = countHits(normalized, rules.addressBangla, rules.addressLatin)
        val district = findDistrict(normalized)
        val weights = rules.score
        var score = 0
        if (phones.isNotEmpty()) score += weights.phone
        score += weights.intent * min(intent, weights.maxIntentHits)
        score += weights.address * min(address, weights.maxAddressHits)
        if (district != null) score += weights.district
        return Result(
            score = score,
            orderLike = phones.isNotEmpty() && score >= weights.threshold,
            phones = phones,
            intentHits = intent,
            addressHits = address,
            district = district,
        )
    }

    private fun findPhones(text: String): List<String> {
        val found = sortedMapOf<Int, String>()
        for (pattern in rules.phone.patterns) {
            for (match in pattern.findAll(text)) {
                val national = toNational(match.value) ?: continue
                if (national !in found.values) found[match.range.first] = national
            }
        }
        return found.values.distinct()
    }

    private fun toNational(span: String): String? {
        val digits = buildString {
            for (char in span) {
                when (char.code) {
                    in '0'.code..'9'.code -> append(char)
                    in BANGLA_ZERO..BANGLA_ZERO + 9 -> append('0' + (char.code - BANGLA_ZERO))
                }
            }
        }
        val national = rules.phone.nationalPattern
        if (national.matches(digits)) return digits
        val prefix = rules.phone.countryPrefix
        if (digits.startsWith(prefix)) {
            val rest = digits.substring(prefix.length)
            if (national.matches(rest)) return rest
        }
        return null
    }

    private fun countHits(text: String, bangla: List<String>, latin: List<String>): Int {
        val matches = mutableListOf<Match>()
        for (word in bangla) occurrences(text, word, wholeWord = false).mapTo(matches) { Match(word, it) }
        for (word in latin) occurrences(text, word, wholeWord = true).mapTo(matches) { Match(word, it) }
        matches.sortWith(compareByDescending<Match> { it.length }.thenBy { it.start })
        val taken = mutableListOf<Match>()
        val words = linkedSetOf<String>()
        for (match in matches) {
            if (taken.any { it.start < match.end && match.start < it.end }) continue
            taken += match
            words += match.word
        }
        return words.size
    }

    private fun findDistrict(text: String): String? {
        var bestName: String? = null
        var bestStart = Int.MAX_VALUE
        var bestLength = 0
        for ((name, spellings) in rules.districts) {
            for (spelling in spellings) {
                val start = occurrences(text, spelling, wholeWord = !IntakeText.isBangla(spelling))
                    .firstOrNull() ?: continue
                // Earliest wins; at the same place the longer spelling ("cox's bazar" over "bazar").
                if (start < bestStart || (start == bestStart && spelling.length > bestLength)) {
                    bestName = name
                    bestStart = start
                    bestLength = spelling.length
                }
            }
        }
        return bestName
    }

    private class Match(val word: String, val start: Int) {
        val length: Int get() = word.length
        val end: Int get() = start + word.length
    }

    companion object {
        private const val BANGLA_ZERO = 0x09E6
        private const val MASK = "•••"

        /** `01700000010` → `01700•••10`: enough to recognise, not to dial. */
        @JvmStatic
        fun maskPhone(national: String): String =
            if (national.length < 8) MASK else national.take(5) + MASK + national.takeLast(2)

        /** Start offsets of [word] in [text]; whole words only when asked. */
        @JvmStatic
        fun occurrences(text: String, word: String, wholeWord: Boolean): List<Int> {
            if (word.isEmpty()) return emptyList()
            val starts = mutableListOf<Int>()
            var from = 0
            while (true) {
                val at = text.indexOf(word, from)
                if (at < 0) break
                val end = at + word.length
                if (!wholeWord || (!isLatinLetter(text, at - 1) && !isLatinLetter(text, end))) {
                    starts += at
                }
                from = at + 1
            }
            return starts
        }

        private fun isLatinLetter(text: String, index: Int): Boolean =
            index in text.indices && text[index] in 'a'..'z'
    }
}
