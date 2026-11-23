import hashlib
import unittest

import repair


def package_entry(**overrides):
    fields = {"Package": "libc6", "Architecture": "amd64", "Version": "2.35-0ubuntu3.18",
              "Filename": "pool/main/g/glibc/libc6_2.35-0ubuntu3.18_amd64.deb",
              "SHA256": "a" * 64, "Size": "123"}
    fields.update(overrides)
    return "\n".join(key + ": " + value for key, value in fields.items())


class PackageValidationTests(unittest.TestCase):
    def test_selects_amd64_package_not_unrelated_entry(self):
        index = "Package: unrelated\nArchitecture: amd64\n\n" + package_entry()
        self.assertEqual(repair.libc_package(index)["Version"], "2.35-0ubuntu3.18")

    def test_rejects_paths_outside_official_glibc_pool(self):
        for path in ("/tmp/libc.deb", "pool/main/g/glibc/../../evil.deb", "https://example.com/a.deb"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                repair.libc_package(package_entry(Filename=path))

    def test_rejects_a_different_glibc_release(self):
        with self.assertRaises(ValueError):
            repair.libc_package(package_entry(Version="2.39-0ubuntu1"))

    def test_rejects_missing_integrity_metadata(self):
        for invalid in ({"SHA256": "bad"}, {"Size": "not-a-number"}, {"Size": "0"}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                repair.libc_package(package_entry(**invalid))

    def test_checks_download_size_and_digest(self):
        data = b"downloaded package"
        meta = {"Size": str(len(data)), "SHA256": hashlib.sha256(data).hexdigest()}
        repair.verify_package(data, meta)
        for invalid in ({**meta, "Size": "1"}, {**meta, "SHA256": "0" * 64}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                repair.verify_package(data, invalid)


class BinaryCompatibilityTests(unittest.TestCase):
    def test_changes_only_exact_symbol_names_without_changing_binary_length(self):
        source = b"header\0" + repair.OLD_GLIB_SYMBOL + b"tail\0" + repair.OLD_GLIB_SYMBOL
        result, count = repair.rewrite_glib_symbol(source)
        self.assertEqual(count, 2)
        self.assertEqual(len(source), len(result))
        self.assertNotIn(repair.OLD_GLIB_SYMBOL, result)
        self.assertEqual(result.count(repair.NEW_GLIB_SYMBOL), 2)

    def test_accepts_already_compatible_plugin(self):
        result, count = repair.rewrite_glib_symbol(repair.NEW_GLIB_SYMBOL)
        self.assertEqual((result, count), (repair.NEW_GLIB_SYMBOL, 0))

    def test_rejects_an_unrecognized_plugin(self):
        with self.assertRaises(ValueError):
            repair.rewrite_glib_symbol(b"different ELF contents")


class LauncherTests(unittest.TestCase):
    def test_desktop_exec_path_is_quoted_and_special_characters_are_escaped(self):
        self.assertEqual(repair.desktop_quote('/home/A B/"$`%/FlClash'),
                         '"/home/A B/\\"\\$\\`%%/FlClash"')


if __name__ == "__main__":
    unittest.main()
