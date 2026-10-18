"""Client name search the assistant shares between the context, the executor and the eval scorer.
Pure functions here - the database side is covered in test_ai_executor / test_ai_assistant."""
import time

import pytest

from app.ai.client_search import fold, rank_in_text, resolve_name, word_match

CLIENTS = [
    (1, "Анна Петрова"),
    (2, "Анна Петренко"),
    (3, "Елена Кузнецова"),
    (4, "Дмитрий Орлов"),
    (5, "Tereza Svobodová"),
    (6, "Tereza Svobodná"),
    (7, "Lucie Černá"),
    (8, "Martin Novák"),
    (9, "Emily Clark"),
    (10, "Emma Clarke"),
    (11, "John Smith"),
    (12, "Ivana Nováková"),
    (13, "Алёна Смирнова"),
    (14, "Сергей Волков"),
]


def found(query, clients=CLIENTS):
    return [name for _, name in resolve_name(query, clients)]


def test_fold_drops_case_accents_and_yo():
    assert fold("Svobodová") == "svobodova"
    assert fold("Алёна") == fold("АЛЕНА") == "алена"
    assert fold("Černá") == "cerna"


@pytest.mark.parametrize("query, who", [
    ("Анну Петрову", "Анна Петрова"),
    ("Петровой Анне", "Анна Петрова"),
    ("Кузнецову Елену", "Елена Кузнецова"),
    ("Елены Кузнецовой", "Елена Кузнецова"),
    ("Алену Смирнову", "Алёна Смирнова"),
    ("Алёной", "Алёна Смирнова"),
    ("Сергея Волкова", "Сергей Волков"),
    ("Дмитрием", "Дмитрий Орлов"),
])
def test_russian_cases_find_the_client(query, who):
    assert found(query) == [who]


@pytest.mark.parametrize("query, who", [
    ("Terezu Svobodovou", "Tereza Svobodová"),
    ("Svobodova", "Tereza Svobodová"),          # typed without the accent
    ("svobodové", "Tereza Svobodová"),
    ("Terezy Svobodné", "Tereza Svobodná"),
    ("Ivanu Novákové", "Ivana Nováková"),
    ("Novakova", "Ivana Nováková"),
    ("Martina Nováka", "Martin Novák"),
    ("Lucii Černou", "Lucie Černá"),
])
def test_czech_cases_and_missing_accents_find_the_client(query, who):
    assert found(query) == [who]


@pytest.mark.parametrize("query, who", [
    ("Emily Clark", ["Emily Clark"]),
    ("emma clarke", ["Emma Clarke"]),
    ("Clarke", ["Emma Clarke"]),             # the plain substring search found only her before, still does
    ("Clark", ["Emily Clark", "Emma Clarke"]),
    ("John", ["John Smith"]),
    ("Emily Cl", ["Emily Clark"]),           # a partial name, like before
    ("Nobody Here", []),
])
def test_english_works_like_the_plain_substring_search(query, who):
    assert found(query) == who


@pytest.mark.parametrize("query", ["на", "do", "к", "a", "  ", "!!", "10"])
def test_short_words_do_not_match_everybody(query):
    assert found(query) == []


@pytest.mark.parametrize("query, who", [
    ("Анну", ["Анна Петрова", "Анна Петренко"]),
    ("Terezu", ["Tereza Svobodová", "Tereza Svobodná"]),
    ("Terezou", ["Tereza Svobodová", "Tereza Svobodná"]),
])
def test_similar_clients_stay_ambiguous(query, who):
    assert found(query) == who


def test_exact_full_name_wins_over_a_longer_one():
    clients = [(1, "Анна Петровская"), (2, "Анна Петрова")]
    assert found("анна петрова", clients) == ["Анна Петрова"]
    assert found("Анну Петрову", clients) == ["Анна Петрова"]
    # two clients with the very same name are still two
    assert len(found("Анна Петрова", clients + [(3, "Анна Петрова")])) == 2


def test_surname_change_does_not_join_different_people():
    assert not word_match("svobodovou", "svobodna")
    assert not word_match("петрову", "петренко")
    assert not word_match("novakova", "novak")


def test_text_ranking_puts_the_named_client_first():
    ids = rank_in_text("Запиши Анну Петрову на стрижку завтра в 10", CLIENTS, limit=10)
    assert ids[0] == 1 and 2 in ids  # Петренко is an Анна too, just lower
    assert rank_in_text("Zapiš Terezu Svobodovou na manikúru", CLIENTS, limit=10)[0] == 5
    assert rank_in_text("Book Emily Clark for a massage", CLIENTS, limit=10)[0] == 9
    assert rank_in_text("Анна Петрова", CLIENTS, limit=1) == [1]


def test_text_ranking_ignores_short_and_unrelated_words():
    assert rank_in_text("на do к в 10 и a", CLIENTS, limit=10) == []
    assert rank_in_text("Запиши на стрижку завтра в 10", CLIENTS, limit=10) == []


def test_ranking_is_stable_and_capped():
    many = [(i, "Анна Петрова") for i in range(30, 0, -1)]
    assert rank_in_text("Анну Петрову", many, limit=5) == [1, 2, 3, 4, 5]


def test_a_few_thousand_clients_is_quick():
    clients = [(i, f"{first} {last}{i % 97}") for i, (first, last) in
               enumerate([("Анна", "Петрова"), ("Tereza", "Svobodová"), ("John", "Smith")] * 1000)]
    start = time.perf_counter()
    rank_in_text("Запиши Анну Петрову на стрижку завтра в 10 и Terezu Svobodovou na manikúru", clients, limit=10)
    resolve_name("Terezu Svobodovou", clients)
    assert time.perf_counter() - start < 1.0
