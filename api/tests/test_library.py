# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Bibliothèque : import sécurisé, ZIP, zone de dépôt, disque, atomicité."""

import io
import json
import os
import shutil
import time
import zipfile
from collections import namedtuple
from pathlib import Path

import pytest

from icc_ai import library as library_module
from icc_ai.library import (
    DEFAULT_CATEGORY, MAX_FILES_PER_UPLOAD, MAX_TAGS, MAX_ZIP_ENTRIES, Library, LibraryError, Limits,
    parse_tags, safe_name, slugify_tag,
)

CODE = b"def f():\n    return 1\n"
OTHER = b"def g():\n    return 2\n"


@pytest.fixture
def make_library(tmp_path):
    def build(**limits) -> Library:
        root = tmp_path / "codes"
        root.mkdir(exist_ok=True)
        values = {"min_free_bytes": 0}
        values.update(limits)
        lib = Library(root, tmp_path / "data", Limits(**values))
        if not lib.supported_extensions():
            pytest.skip("module icc_crawler indisponible")
        return lib

    return build


@pytest.fixture
def lib(make_library):
    return make_library()


def make_zip(entries, *, links=()):
    """ZIP en mémoire : `entries` = {nom: octets}, `links` = {nom: cible}."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
        for name, target in dict(links).items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (0o120777 << 16)
            archive.writestr(info, target)
    return buffer.getvalue()


def files_under(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file() and p.name != library_module.MARKER]


# --------------------------------------------------------------------------
# Assainissement des noms et des tags
# --------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("../../etc/passwd.py", "passwd.py"),
    ("/etc/shadow.py", "shadow.py"),
    ("..\\..\\win.py", "win.py"),
    (".hidden.py", "hidden.py"),
    ("a b$c.py", "a_b_c.py"),
])
def test_safe_name_assainit(raw, expected):
    assert safe_name(raw) == expected


@pytest.mark.parametrize("raw", ["", "..", ".", ".bashrc", "sans_extension", "x" * 130 + ".py", "dossier/"])
def test_safe_name_refuse(raw):
    assert safe_name(raw) is None


@pytest.mark.parametrize("raw,expected", [
    ("../etc", "etc"), ("..", None), ("/", None), ("A/B", "a-b"), ("Crypté", "crypte"),
    ("  API  ", "api"), ("a..b", "a.b"), ("x" * 100, "x" * 40), ("!!!", None),
])
def test_slugify_tag(raw, expected):
    assert slugify_tag(raw) == expected


def test_parse_tags_borne_et_sans_doublon():
    assert parse_tags("API, python  ; Crypto\n#api") == ["api", "python", "crypto"]
    assert parse_tags(None) == [] and parse_tags("") == []
    assert len(parse_tags([f"t{i}" for i in range(20)])) == MAX_TAGS
    assert parse_tags(["../../x", "/abs"]) == ["x", "abs"]


def test_limites_depuis_l_environnement(monkeypatch):
    monkeypatch.setenv("CODECENTER_IMPORT_MAX_FILE_KB", "10")
    monkeypatch.setenv("CODECENTER_IMPORT_MAX_UPLOAD_MB", "abc")
    monkeypatch.setenv("CODECENTER_MIN_FREE_GB", "0.5")
    limits = Limits.from_environment()
    assert limits.max_file_bytes == 10_000
    assert limits.max_upload_bytes == 50_000_000  # valeur invalide -> défaut
    assert limits.min_free_bytes == 512 * 1024**2


def test_racine_depuis_l_environnement(monkeypatch, tmp_path):
    monkeypatch.setenv("CODECENTER_WATCH_PATHS", f"{tmp_path / 'a'}{os.pathsep}{tmp_path / 'b'}")
    assert Library.from_environment(tmp_path / "d").root == tmp_path / "a"
    monkeypatch.setenv("CODECENTER_WATCH_PATHS", "")
    assert Library.from_environment(tmp_path / "d").root == tmp_path / "d" / "codes"


# --------------------------------------------------------------------------
# Import d'un fichier : erreurs d'abord
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name,data,code", [
    ("", CODE, "bad_name"),
    ("..", CODE, "bad_name"),
    (".bashrc", CODE, "bad_name"),
    ("notes.txt", b"bonjour", "unsupported"),
    ("prog.exe", b"MZ", "unsupported"),
    ("vide.py", b"", "empty"),
    ("bin.py", b"ab\x00cd", "binary"),
    ("latin1.py", "caf\xe9".encode("latin-1"), "encoding"),
    ("faux.py", b"\xff\xfe\x80\x81", "encoding"),
])
def test_import_refuse(lib, name, data, code):
    with pytest.raises(LibraryError) as err:
        lib.import_bytes(name, data, ["t"])
    assert err.value.code == code
    assert files_under(lib.root) == []


def test_import_trop_gros(make_library):
    lib = make_library(max_file_bytes=100)
    assert lib.import_bytes("ok.py", b"x = 1\n" * 10, ["t"])
    with pytest.raises(LibraryError) as err:
        lib.import_bytes("gros.py", b"x = 1\n" * 100, ["t"])
    assert err.value.code == "too_large"


def test_import_utf8_avec_bom_accepte(lib):
    saved = lib.import_bytes("bom.py", b"\xef\xbb\xbfx = 1\n", ["t"])
    assert saved["language"] == "python"


@pytest.mark.parametrize("name", ["../../evil.py", "/tmp/evil_abs.py", "..\\..\\evil_win.py", ".cache.py"])
def test_nom_dangereux_reste_dans_la_racine(lib, tmp_path, name):
    saved = lib.import_bytes(name, CODE, ["t"])
    target = (lib.root / saved["path"]).resolve()
    assert lib.root.resolve() in target.parents
    assert not saved["name"].startswith(".")
    assert not (tmp_path / "evil.py").exists() and not Path("/tmp/evil_abs.py").exists()


@pytest.mark.parametrize("tags", [["../../dehors"], ["/etc"], ["a", "../../.."], [".."]])
def test_tag_dangereux_donne_hors_racine(lib, tmp_path, tags):
    with pytest.raises(LibraryError) as err:
        lib.import_bytes("a.py", CODE, tags)
    assert err.value.code == "outside_root"
    assert not (tmp_path / "dehors").exists()


def test_tags_bruts_assainis_par_import_upload(lib, tmp_path):
    report = lib.import_upload([("a.py", CODE)], "../../dehors, /etc")
    assert report.saved and not report.skipped
    assert report.saved[0]["tags"] == ["dehors", "etc"]
    assert not (tmp_path / "dehors").exists()


def test_symlink_sortant_refuse(lib, tmp_path):
    dehors = tmp_path / "dehors"
    dehors.mkdir()
    (lib.root / "lien").symlink_to(dehors, target_is_directory=True)
    with pytest.raises(LibraryError) as err:
        lib.import_bytes("a.py", CODE, ["lien"])
    assert err.value.code == "outside_root"
    assert list(dehors.iterdir()) == []


def test_symlink_de_langage_sortant_refuse(lib, tmp_path):
    dehors = tmp_path / "dehors"
    dehors.mkdir()
    (lib.root / "t").mkdir()
    (lib.root / "t" / "python").symlink_to(dehors, target_is_directory=True)
    with pytest.raises(LibraryError) as err:
        lib.import_bytes("a.py", CODE, ["t"])
    assert err.value.code == "outside_root"
    assert list(dehors.iterdir()) == []


def test_fichier_cible_symlink_ne_modifie_pas_l_exterieur(lib, tmp_path):
    secret = tmp_path / "secret.py"
    secret.write_bytes(b"SECRET = 1\n")
    directory = lib.root / "t" / "python"
    directory.mkdir(parents=True)
    (directory / "a.py").symlink_to(secret)
    saved = lib.import_bytes("a.py", CODE, ["t"])
    assert secret.read_bytes() == b"SECRET = 1\n"
    assert saved["name"] != "a.py"


# --------------------------------------------------------------------------
# Rangement, doublons, atomicité, disque
# --------------------------------------------------------------------------

def test_rangement_par_tags_et_langage(lib):
    saved = lib.import_bytes("tool.py", CODE, ["api", "crypto"])
    assert saved["path"] == "api/crypto/python/tool.py"
    assert (lib.root / "api/crypto/python/tool.py").read_bytes() == CODE
    assert (lib.root / "api/crypto/python" / library_module.MARKER).read_text() == "python"


def test_sans_tag_classement_automatique_ou_par_defaut(lib):
    saved = lib.import_bytes("tool.py", CODE, [])
    assert saved["tags"] and saved["tags"][0] == saved["path"].split("/")[0]
    other = lib.import_bytes("tool2.py", CODE, [], auto_classify=False)
    assert other["tags"] == [DEFAULT_CATEGORY] and other["path"].startswith(DEFAULT_CATEGORY + "/")


def test_doublon_meme_contenu_ignore(lib):
    lib.import_bytes("a.py", CODE, ["t"])
    with pytest.raises(LibraryError) as err:
        lib.import_bytes("a.py", CODE, ["t"])
    assert err.value.code == "duplicate"
    assert len(files_under(lib.root)) == 1


def test_contenu_different_suffixe_sha_sans_ecraser(lib):
    import hashlib
    first = lib.import_bytes("a.py", CODE, ["t"])
    second = lib.import_bytes("a.py", OTHER, ["t"])
    digest = hashlib.sha256(OTHER).hexdigest()[:8]
    assert first["name"] == "a.py" and second["name"] == f"a-{digest}.py"
    assert (lib.root / first["path"]).read_bytes() == CODE
    assert (lib.root / second["path"]).read_bytes() == OTHER
    with pytest.raises(LibraryError) as err:  # le même contenu différent revient : doublon
        lib.import_bytes("a.py", OTHER, ["t"])
    assert err.value.code == "duplicate"


def test_ecriture_atomique_sans_part_residuel(lib):
    lib.import_bytes("a.py", CODE, ["t"])
    assert not list(lib.root.rglob("*.part"))


def test_echec_de_remplacement_ne_laisse_pas_de_part(lib, monkeypatch):
    def broken(src, dst):
        raise OSError("disque en panne")

    monkeypatch.setattr(library_module.os, "replace", broken)
    with pytest.raises(OSError):
        lib.import_bytes("a.py", CODE, ["t"])
    assert not list(lib.root.rglob("*.part"))
    assert files_under(lib.root) == []


def test_disque_presque_plein(make_library, monkeypatch):
    lib = make_library(min_free_bytes=10_000)
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda path: usage(100_000, 99_500, 500))
    with pytest.raises(LibraryError) as err:
        lib.import_bytes("a.py", CODE, ["t"])
    assert err.value.code == "disk_full"
    assert str(lib.root) not in err.value.message
    assert files_under(lib.root) == []
    report = lib.import_upload([("a.py", CODE)], "t")
    assert report.skipped[0]["code"] == "disk_full" and not report.saved


def test_disque_la_place_du_fichier_compte(make_library, monkeypatch):
    lib = make_library(min_free_bytes=1000)
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda path: usage(10**6, 0, 1000 + len(CODE) - 1))
    with pytest.raises(LibraryError):
        lib.import_bytes("a.py", CODE, ["t"])
    monkeypatch.setattr(shutil, "disk_usage", lambda path: usage(10**6, 0, 1000 + len(CODE)))
    assert lib.import_bytes("a.py", CODE, ["t"])


# --------------------------------------------------------------------------
# import_upload
# --------------------------------------------------------------------------

def test_upload_erreurs_globales(make_library):
    lib = make_library(max_upload_bytes=100)
    with pytest.raises(LibraryError) as err:
        lib.import_upload([], "t")
    assert err.value.code == "no_files"
    with pytest.raises(LibraryError) as err:
        lib.import_upload([(f"f{i}.py", b"x=1\n") for i in range(MAX_FILES_PER_UPLOAD + 1)], "t")
    assert err.value.code == "too_many"
    with pytest.raises(LibraryError) as err:
        lib.import_upload([("a.py", b"x" * 101)], "t")
    assert err.value.code == "upload_too_large"


def test_upload_rapport_melange_succes_et_refus(lib):
    report = lib.import_upload(
        [("ok.py", CODE), ("../../x.txt", b"hello"), ("bin.py", b"\x00\x01"), ("ok.py", CODE)], "t")
    codes = {s["code"] for s in report.skipped}
    assert [s["name"] for s in report.saved] == ["ok.py"]
    assert codes == {"unsupported", "binary", "duplicate"}
    data = report.as_dict()
    assert data["saved_count"] == 1 and data["skipped_count"] == 3
    # Le nom rapporté est le dernier composant, pas le chemin envoyé.
    assert all("/" not in s["name"] for s in report.skipped)


def test_upload_erreur_d_ecriture_signalee_sans_chemin(lib, monkeypatch):
    def broken(self, *a, **k):
        raise PermissionError(13, "Permission denied", str(lib.root))

    monkeypatch.setattr(Path, "mkdir", broken)
    report = lib.import_upload([("a.py", CODE)], "t")
    assert report.skipped[0]["code"] == "io" and str(lib.root) not in json.dumps(report.as_dict())


# --------------------------------------------------------------------------
# ZIP
# --------------------------------------------------------------------------

def test_zip_normal_avec_dossiers_en_tags(lib):
    data = make_zip({"crypto/aes.py": CODE, "web/app/server.rs": b"fn main() {}\n", "LISEZ.md": b"# x"})
    report = lib.import_upload([("archive.zip", data)], "base")
    paths = sorted(s["path"] for s in report.saved)
    assert paths == ["base/crypto/python/aes.py", "base/web/app/rust/server.rs"]
    assert not report.skipped  # le .md est ignoré en silence


def test_zip_sans_conserver_les_dossiers(lib):
    report = lib.import_upload([("a.ZIP", make_zip({"crypto/aes.py": CODE}))], "base", keep_folders=False)
    assert [s["path"] for s in report.saved] == ["base/python/aes.py"]


@pytest.mark.parametrize("name", ["../evil.py", "/abs_evil.py", "a/../../evil.py", "..\\evil.py", "a/..\\..\\evil.py"])
def test_zip_slip_refuse(lib, tmp_path, name):
    report = lib.import_upload([("a.zip", make_zip({name: CODE, "bon.py": OTHER}))], "t")
    assert [s["name"] for s in report.saved] == ["bon.py"]
    assert [s["code"] for s in report.skipped] == ["zip_slip"]
    assert not (tmp_path / "evil.py").exists() and not Path("/abs_evil.py").exists()


def test_zip_lien_symbolique_ignore(lib, tmp_path):
    (tmp_path / "secret.py").write_text("SECRET = 1\n")
    data = make_zip({"bon.py": CODE}, links={"lien.py": str(tmp_path / "secret.py")})
    report = lib.import_upload([("a.zip", data)], "t")
    assert [s["name"] for s in report.saved] == ["bon.py"]
    assert report.skipped[0]["code"] == "zip_link"
    assert not any("SECRET" in p.read_text() for p in files_under(lib.root))


def test_zip_fichiers_caches_et_macosx_ignores(lib):
    data = make_zip({".git/config.py": CODE, "__MACOSX/a.py": CODE, "d/.cache.py": CODE, "ok.py": OTHER})
    report = lib.import_upload([("a.zip", data)], "t")
    assert [s["name"] for s in report.saved] == ["ok.py"]
    assert report.skipped == []


def test_zip_illisible_et_trop_riche(lib):
    report = lib.import_upload([("a.zip", b"pas un zip")], "t")
    assert report.skipped[0]["code"] == "bad_zip"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for i in range(MAX_ZIP_ENTRIES + 1):
            archive.writestr(f"f{i}.py", "x")
    report = lib.import_upload([("a.zip", buffer.getvalue())], "t")
    assert report.skipped[0]["code"] == "zip_too_many" and not report.saved


def test_zip_bombe_refusee_sans_decompression(make_library):
    lib = make_library(max_file_bytes=10_000, max_upload_bytes=1_000_000)
    bomb = make_zip({"bombe.py": b"a" * 50_000_000, "ok.py": CODE})
    assert len(bomb) < 100_000  # 50 Mo une fois décompressé
    report = lib.import_upload([("b.zip", bomb)], "t")
    assert [s["name"] for s in report.saved] == ["ok.py"]
    assert report.skipped[0]["code"] == "too_large"


def test_zip_lecture_reelle_bornee(make_library, monkeypatch):
    lib = make_library(max_file_bytes=1000)
    sizes = []
    original = zipfile.ZipExtFile.read

    def spy(self, n=-1):
        sizes.append(n)
        return original(self, n)

    monkeypatch.setattr(zipfile.ZipExtFile, "read", spy)
    lib.import_upload([("a.zip", make_zip({"ok.py": CODE}))], "t")
    assert sizes and all(0 < n <= 1001 for n in sizes)


def test_zip_en_tete_mensonger_ne_plante_pas(make_library):
    """Taille déclarée fausse : la lecture est bornée et l'erreur est rapportée, jamais propagée."""
    lib = make_library(max_file_bytes=100)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("menteur.py", b"x = 1\n" * 5000)
        archive.writestr("ok.py", CODE)
        archive.filelist[0].file_size = 10  # l'en-tête annonce 10 octets
    report = lib.import_upload([("m.zip", buffer.getvalue())], "t")
    assert [s["name"] for s in report.saved] == ["ok.py"]
    assert [s["code"] for s in report.skipped] == ["zip_corrupt"]


def test_zip_budget_total_borne(make_library):
    lib = make_library(max_file_bytes=1000, max_upload_bytes=1500)
    entries = {f"f{i}.py": (b"v%d = 1\n" % i) * 100 for i in range(5)}  # ~800 octets chacun
    report = lib.import_upload([("a.zip", make_zip(entries))], "t")
    assert 1 <= len(report.saved) < 5
    assert {s["code"] for s in report.skipped} == {"too_large"}


def test_zip_entree_chiffree_rapportee(lib):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("secret.py", CODE)
        archive.writestr("ok.py", OTHER)
        archive.filelist[0].flag_bits |= 0x1  # marquée chiffrée
    report = lib.import_upload([("e.zip", buffer.getvalue())], "t")
    assert [s["name"] for s in report.saved] == ["ok.py"]
    assert [s["code"] for s in report.skipped] == ["zip_corrupt"]


# --------------------------------------------------------------------------
# Zone de dépôt
# --------------------------------------------------------------------------

def put(depot: Path, relative: str, data: bytes = CODE, age: float = 100.0) -> Path:
    path = depot / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    stamp = time.time() - age
    os.utime(path, (stamp, stamp))
    return path


def test_depot_cree_avec_mode_d_emploi(lib):
    lib.ensure_depot()
    assert (lib.depot / "LISEZ-MOI.txt").is_file()
    assert lib.scan_depot(settle_seconds=0).saved == []  # le mode d'emploi n'est jamais importé


def test_depot_absent_sans_effet(lib):
    report = lib.scan_depot()
    assert report.saved == [] and report.skipped == []


def test_depot_tags_par_sous_dossier_txt_et_sidecar(lib):
    lib.ensure_depot()
    put(lib.depot, "crypto/aes.py")
    put(lib.depot, "groupe/tags.txt", "# commentaire\napi, Réseau\n".encode())
    put(lib.depot, "groupe/net.py", OTHER)
    put(lib.depot, "seul.py", b"z = 3\n")
    put(lib.depot, "seul.py.tags", b"perso;outil")
    put(lib.depot, "tags.txt", b"global")
    put(lib.depot, "racine.py", b"y = 2\n")
    report = lib.scan_depot(settle_seconds=0)
    paths = {s["name"]: s["path"] for s in report.saved}
    assert paths["aes.py"] == "global/crypto/python/aes.py"
    assert paths["net.py"] == "api/reseau/groupe/python/net.py"
    assert paths["seul.py"] == "perso/outil/python/seul.py"
    assert paths["racine.py"] == "global/python/racine.py"
    assert not report.skipped


def test_depot_source_et_sidecar_supprimes_apres_rangement(lib):
    lib.ensure_depot()
    source = put(lib.depot, "a.py")
    sidecar = put(lib.depot, "a.py.tags", b"x")
    lib.scan_depot(settle_seconds=0)
    assert not source.exists() and not sidecar.exists()
    assert (lib.root / "x/python/a.py").is_file()
    assert not (lib.data_dir / "depot-ledger.json").exists()


def test_depot_fichiers_trop_recents_ignores(lib):
    lib.ensure_depot()
    recent = put(lib.depot, "neuf.py", age=0)
    assert lib.scan_depot(settle_seconds=60).saved == [] and recent.exists()
    assert len(lib.scan_depot(settle_seconds=0).saved) == 1 and not recent.exists()


def test_depot_ignore_caches_liens_et_extensions(lib, tmp_path):
    lib.ensure_depot()
    put(lib.depot, ".cache/x.py")
    put(lib.depot, "d/.cache.py")
    put(lib.depot, "notes.txt", b"hello")
    secret = tmp_path / "secret.py"
    secret.write_text("SECRET = 1\n")
    (lib.depot / "lien.py").symlink_to(secret)
    report = lib.scan_depot(settle_seconds=0)
    assert report.saved == [] and report.skipped == []
    assert secret.exists() and (lib.depot / "notes.txt").exists()


def test_depot_refus_rapportes_par_fichier(lib):
    lib.ensure_depot()
    put(lib.depot, "bin.py", b"\x00\x01")
    put(lib.depot, "ok.py")
    report = lib.scan_depot(settle_seconds=0)
    assert [s["name"] for s in report.saved] == ["ok.py"]
    assert report.skipped[0]["code"] == "binary" and report.skipped[0]["name"] == "bin.py"


def test_depot_suppression_impossible_registre_evite_le_retraitement(lib, monkeypatch):
    lib.ensure_depot()
    source = put(lib.depot, "a.py")
    real_unlink = Path.unlink

    def refuse(self, *args, **kwargs):
        if self == source:
            raise PermissionError(13, "dossier en lecture seule")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", refuse)
    first = lib.scan_depot(settle_seconds=0)
    assert len(first.saved) == 1 and source.exists()
    ledger = json.loads((lib.data_dir / "depot-ledger.json").read_text())
    assert len(ledger) == 1 and "a.py" in next(iter(ledger))

    second = lib.scan_depot(settle_seconds=0)  # ni doublon rapporté ni nouvelle copie
    assert second.saved == [] and second.skipped == []
    assert len(files_under(lib.root / "importes")) + len(files_under(lib.root)) >= 1
    assert len([p for p in files_under(lib.root) if p.suffix == ".py" and "_depot" not in p.parts]) == 1


def test_depot_fichier_modifie_apres_echec_de_suppression_est_retraite(lib, monkeypatch):
    lib.ensure_depot()
    source = put(lib.depot, "a.py")
    real_unlink = Path.unlink
    monkeypatch.setattr(Path, "unlink", lambda self, *a, **k: (_ for _ in ()).throw(PermissionError())
                        if self == source else real_unlink(self, *a, **k))
    lib.scan_depot(settle_seconds=0)
    source.write_bytes(OTHER)
    stamp = time.time() - 50
    os.utime(source, (stamp, stamp))
    report = lib.scan_depot(settle_seconds=0)
    assert len(report.saved) == 1  # nouveau contenu : suffixe sha, rien d'écrasé


def test_depot_registre_corrompu_toléré(lib):
    lib.ensure_depot()
    lib.data_dir.mkdir(parents=True, exist_ok=True)
    (lib.data_dir / "depot-ledger.json").write_text("{pas du json")
    put(lib.depot, "a.py")
    assert len(lib.scan_depot(settle_seconds=0).saved) == 1


def test_depot_disque_plein_arrete_et_conserve_les_sources(make_library, monkeypatch):
    lib = make_library(min_free_bytes=10_000)
    lib.ensure_depot()
    a, b = put(lib.depot, "a.py"), put(lib.depot, "b.py", OTHER)
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda path: usage(1, 1, 5))
    report = lib.scan_depot(settle_seconds=0)
    assert [s["code"] for s in report.skipped] == ["disk_full"]  # arrêt dès le premier
    assert a.exists() and b.exists()
