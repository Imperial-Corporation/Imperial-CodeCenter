# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Parcours de l'interface : compte, jetons d'API, administration, langue."""

import pytest

from conftest import FakeApi


def open_account(page):
    page.click("#account-button")
    page.wait_for_selector("dialog.auth-dialog[open]")


def test_page_charge_sans_erreur(make_page):
    page, _ = make_page(FakeApi(user=None))
    page.wait_for_selector("#search-input")
    assert page.inner_text("h1") == "Imperial-CodeCenter"
    assert page.errors == []


def test_visiteur_voit_connexion_et_pas_ladministration(make_page):
    page, _ = make_page(FakeApi(user=None))
    open_account(page)
    assert "Connexion" in page.inner_text("dialog")
    assert page.locator("dialog button:has-text('Administration')").count() == 0


def test_utilisateur_simple_sans_bouton_administration(make_page):
    page, _ = make_page(FakeApi(admin=False))
    page.wait_for_function("document.querySelector('#account-label').textContent === 'alice'")
    open_account(page)
    assert page.locator("dialog button:has-text('Administration')").count() == 0
    assert page.locator("dialog >> text=Jetons d'API").count() == 1


def test_creation_et_revocation_de_jeton(make_page):
    page, api = make_page()
    page.wait_for_function("document.querySelector('#account-label').textContent === 'alice'")
    open_account(page)
    page.click("dialog summary:has-text(\"Jetons d'API\")")
    page.fill("dialog input[placeholder^='Nom du jeton']", "portable")
    page.click("dialog button:has-text('Créer un jeton')")
    page.wait_for_selector("dialog code.adm-token")
    assert page.inner_text("dialog code.adm-token") == "icc_pat_t1_SECRETVALUE"
    page.wait_for_selector("dialog li:has-text('portable')")
    page.click("dialog li:has-text('portable') >> text=Révoquer")
    page.wait_for_selector("dialog li:has-text('Aucun jeton')")
    assert ("DELETE", "/auth/tokens/t1") in api.calls


def test_administration_comptes_et_inscriptions(make_page):
    page, api = make_page()
    page.wait_for_function("document.querySelector('#account-label').textContent === 'alice'")
    open_account(page)
    page.click("dialog button:has-text('Administration')")
    page.wait_for_selector("dialog.adm-dialog[open] table")
    assert page.locator("dialog.adm-dialog tbody tr").count() == 2
    # l'administrateur n'a aucune action destructrice
    assert page.locator("dialog.adm-dialog tbody tr").nth(0).locator("button").count() == 0
    page.click("dialog.adm-dialog tbody tr:has-text('bob') >> text=Désactiver")
    page.wait_for_selector("dialog.adm-dialog tr[data-disabled='true']")
    page.uncheck("dialog.adm-dialog input[type=checkbox]")
    page.wait_for_selector("dialog.adm-dialog .adm-status[data-kind='ok']")
    assert api.registration_open is False


def test_administration_index_rescan_et_exclusions(make_page):
    page, api = make_page()
    page.wait_for_function("document.querySelector('#account-label').textContent === 'alice'")
    open_account(page)
    page.click("dialog button:has-text('Administration')")
    page.click("dialog.adm-dialog .auth-tab:has-text('Index')")
    page.wait_for_selector("dialog.adm-dialog textarea")
    assert page.input_value("dialog.adm-dialog textarea") == "*.min.js"
    page.fill("dialog.adm-dialog textarea", "*.min.js\nnode_modules\n")
    page.click("dialog.adm-dialog button:has-text('Enregistrer les exclusions')")
    page.wait_for_selector("dialog.adm-dialog .adm-status[data-kind='ok']")
    assert api.exclusions == ["*.min.js", "node_modules"]
    page.click("dialog.adm-dialog button:has-text('Relancer')")
    page.wait_for_selector("dialog.adm-dialog :text('Passage en cours')")


def test_administration_systeme_et_sauvegardes(make_page):
    page, api = make_page()
    page.wait_for_function("document.querySelector('#account-label').textContent === 'alice'")
    open_account(page)
    page.click("dialog button:has-text('Administration')")
    page.click("dialog.adm-dialog .auth-tab:has-text('Système')")
    page.wait_for_selector("dialog.adm-dialog :text('Profil : lite')")
    page.click("dialog.adm-dialog .auth-tab:has-text('Sauvegardes')")
    page.click("dialog.adm-dialog button:has-text('Créer une sauvegarde')")
    page.wait_for_selector("dialog.adm-dialog a:has-text('icc-backup-')")


def test_langue_francais_anglais_et_retour(make_page):
    page, _ = make_page(FakeApi(user=None), locale="fr-FR")
    assert page.inner_text("#new-chat") == "Nouvelle discussion"
    page.click("#lang-toggle button[data-lang=en]")
    assert page.inner_text("#new-chat") == "New chat"
    assert page.get_attribute("#search-input", "placeholder").startswith("Search")
    assert page.evaluate("document.documentElement.lang") == "en"
    open_account(page)  # contenu créé après coup : traduit aussi
    assert "Sign in" in page.inner_text("dialog")
    page.keyboard.press("Escape")
    page.click("#lang-toggle button[data-lang=fr]")
    assert page.inner_text("#new-chat") == "Nouvelle discussion"


def test_langue_du_navigateur_anglais_par_defaut(make_page):
    page, _ = make_page(FakeApi(user=None), locale="en-US")
    assert page.inner_text("#new-chat") == "New chat"
