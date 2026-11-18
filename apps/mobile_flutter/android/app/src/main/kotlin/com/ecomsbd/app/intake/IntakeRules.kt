package com.ecomsbd.app.intake

import org.json.JSONArray
import org.json.JSONObject
import java.text.Normalizer

/**
 * The detection rules the server publishes (`GET /v1/intake/rules`, V4 WP1.2),
 * parsed once and kept ready for [IntakeClassifier].
 *
 * Only schema version 1 is understood. A body this build cannot read throws
 * [UnsupportedRulesException]; the caller then keeps the copy it already has,
 * so a new rule format never turns detection off on old installs by accident.
 *
 * No `android.*` imports: this file runs in JVM unit tests unchanged.
 */
class IntakeRules(
    val version: Int,
    val packages: List<PackageRule>,
    /** Packages this shop reads, already without the ones an official connection covers. */
    val enabledPackages: Set<String>,
    val phone: PhoneRules,
    val intentBangla: List<String>,
    val intentLatin: List<String>,
    val addressBangla: List<String>,
    val addressLatin: List<String>,
    /** Canonical district name → every spelling (normalised like message text). */
    val districts: Map<String, List<String>>,
    val score: ScoreRules,
    val bufferWindowMillis: Long,
    val refreshAfterMillis: Long,
) {
    data class PackageRule(val pkg: String, val label: String, val defaultEnabled: Boolean)

    class PhoneRules(
        val patterns: List<Regex>,
        val nationalPattern: Regex,
        val countryPrefix: String,
    )

    data class ScoreRules(
        val phone: Int,
        val intent: Int,
        val address: Int,
        val district: Int,
        val maxIntentHits: Int,
        val maxAddressHits: Int,
        val threshold: Int,
    )

    class UnsupportedRulesException(message: String) : Exception(message)

    companion object {
        const val SCHEMA_VERSION = 1

        /** Parse the exact bytes the server sent. Throws on anything unexpected. */
        @JvmStatic
        fun parse(body: String): IntakeRules {
            try {
                return parseOrThrow(JSONObject(body))
            } catch (error: UnsupportedRulesException) {
                throw error
            } catch (error: Exception) {
                // org.json and regex errors carry fragments of the body: keep them out.
                throw UnsupportedRulesException("rules body is not readable")
            }
        }

        private fun parseOrThrow(root: JSONObject): IntakeRules {
            val schema = root.getInt("schema_version")
            if (schema != SCHEMA_VERSION) {
                throw UnsupportedRulesException("schema_version $schema is not supported")
            }
            val rules = root.getJSONObject("rules")
            val phone = rules.getJSONObject("phone")
            val intent = rules.getJSONObject("intent")
            val address = rules.getJSONObject("address")
            val score = rules.getJSONObject("score")
            val weights = score.getJSONObject("weights")
            val maxHits = score.getJSONObject("max_hits")
            val listId = address.getString("district_list")
            val districtList = root.getJSONObject("district_lists").getJSONObject(listId)
            val districts = linkedMapOf<String, List<String>>()
            for (name in districtList.keys().asSequence().sorted()) {
                districts[name] = strings(districtList.getJSONArray(name)).map(IntakeText::normalize)
            }
            return IntakeRules(
                version = root.getInt("version"),
                packages = objects(rules.getJSONArray("packages")).map {
                    PackageRule(
                        pkg = it.getString("package"),
                        label = it.getString("label"),
                        defaultEnabled = it.getBoolean("default_enabled"),
                    )
                },
                enabledPackages = strings(root.getJSONArray("enabled_packages")).toSet(),
                phone = PhoneRules(
                    patterns = strings(phone.getJSONArray("patterns")).map { Regex(it) },
                    nationalPattern = Regex(phone.getString("national_pattern")),
                    countryPrefix = phone.getString("country_prefix"),
                ),
                intentBangla = keywords(intent.getJSONArray("bn")),
                intentLatin = keywords(intent.getJSONArray("banglish")) +
                    keywords(intent.getJSONArray("en")),
                addressBangla = keywords(address.getJSONArray("bn")),
                addressLatin = keywords(address.getJSONArray("latin")),
                districts = districts,
                score = ScoreRules(
                    phone = weights.getInt("phone"),
                    intent = weights.getInt("intent"),
                    address = weights.getInt("address"),
                    district = weights.getInt("district"),
                    maxIntentHits = maxHits.getInt("intent"),
                    maxAddressHits = maxHits.getInt("address"),
                    threshold = score.getInt("threshold"),
                ),
                bufferWindowMillis = rules.getLong("buffer_window_minutes") * 60_000L,
                refreshAfterMillis = root.getLong("refresh_after_seconds") * 1_000L,
            )
        }

        private fun strings(array: JSONArray): List<String> =
            (0 until array.length()).map { array.getString(it) }

        private fun objects(array: JSONArray): List<JSONObject> =
            (0 until array.length()).map { array.getJSONObject(it) }

        private fun keywords(array: JSONArray): List<String> =
            strings(array).map(IntakeText::normalize).filter { it.isNotEmpty() }.distinct()
    }
}

/** The one text normalisation both rules and messages go through. */
object IntakeText {
    private val spaces = Regex("[\\s\\u00A0\\u2007\\u202F]+")

    /** NFC, lower-case, every run of white space as one space, trimmed. */
    @JvmStatic
    fun normalize(text: String): String =
        Normalizer.normalize(text, Normalizer.Form.NFC)
            .lowercase(java.util.Locale.ROOT)
            .replace(spaces, " ")
            .trim()

    /** True when the keyword is written in Bangla (matched as a substring). */
    @JvmStatic
    fun isBangla(word: String): Boolean = word.any { it.code in 0x0980..0x09FF }
}
