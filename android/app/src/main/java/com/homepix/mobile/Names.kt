package com.homepix.mobile

/** Words that only join names to each other ("Mom and Dad", "photos of Sam with Ben"). */
private val JOINING = setOf("and", "with", "of", "photos", "photo", "pictures", "picture", "pics")

/**
 * The people named in a search, and what's left of it to search by meaning. Only full names count,
 * so a first name that's also a word ("rose garden") is searched by meaning. Longest names are found
 * first. Each group is one person. With no names, the search comes back as typed.
 */
fun namesIn(q: String, people: List<Person>): Pair<List<List<Person>>, String> {
    var rest = " " + q.lowercase().replace(Regex("[,&+]"), " ").split(Regex("\\s+")).joinToString(" ") + " "
    val groups = mutableListOf<List<Person>>()
    for (p in people.sortedByDescending { it.name.length }) {
        val name = " " + p.name.lowercase().trim().split(Regex("\\s+")).joinToString(" ") + " "
        if (name.isNotBlank() && name in rest) {
            groups += listOf(p)
            rest = rest.replaceFirst(name, " ")
        }
    }
    if (groups.isEmpty()) return groups to q.trim()
    return groups to rest.split(' ').filter { it.isNotEmpty() && it !in JOINING }.joinToString(" ")
}
