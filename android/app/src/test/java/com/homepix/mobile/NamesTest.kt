package com.homepix.mobile

import org.junit.Assert.assertEquals
import org.junit.Test

class NamesTest {
    private val anna = Person("Anna Lee", emptyList(), null)
    private val annaB = Person("Anna Berg", emptyList(), null)
    private val sam = Person("Sam", emptyList(), null)
    private val people = listOf(anna, annaB, sam)

    private fun names(q: String) = namesIn(q, people).let { (g, rest) -> g.map { it.map(Person::name) } to rest }

    @Test
    fun aNameAlone() = assertEquals(listOf(listOf("Sam")) to "", names("sam"))

    @Test
    fun aFullNameBeatsTheFirstName() = assertEquals(listOf(listOf("Anna Lee")) to "", names("Anna Lee"))

    @Test
    fun aFirstNameAloneIsntAFilter() = assertEquals(emptyList<List<String>>() to "anna at the beach", names("anna at the beach"))

    @Test
    fun twoNamesAndWhatTheyreDoing() =
        assertEquals(listOf(listOf("Anna Lee"), listOf("Sam")) to "at the beach", names("Anna Lee and Sam at the beach"))

    @Test
    fun noNamesLeavesTheSearchAlone() = assertEquals(emptyList<List<String>>() to "photos of the beach", names("photos of the beach"))

    @Test
    fun partOfAWordIsntAName() = assertEquals(emptyList<List<String>>() to "samples", names("samples"))
}
