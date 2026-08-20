"""游戏模式命名空间测试。"""

from __future__ import annotations

import unittest

from lol_event_semantics.mode_namespace import mode_namespace_for_token, split_mode_entity_alias


class ModeNamespaceTests(unittest.TestCase):
    """锁定已确认的模式前缀，不泛化未知下划线别名。"""

    def test_resolves_confirmed_prefix_tokens(self) -> None:
        expected = {
            "Jade": ("jade", "经典模式"),
            "ruby": ("ruby", "末日人机"),
            "Strawberry_": ("strawberry", "PVE 模式"),
        }

        for raw, pair in expected.items():
            with self.subTest(raw=raw):
                namespace = mode_namespace_for_token(raw)

                self.assertIsNotNone(namespace)
                self.assertEqual((namespace.key, namespace.label), pair)

    def test_splits_only_confirmed_mode_entity_aliases(self) -> None:
        resolved = split_mode_entity_alias("Jade_Wukong")

        self.assertIsNotNone(resolved)
        namespace, entity_alias = resolved
        self.assertEqual(namespace.key, "jade")
        self.assertEqual(entity_alias, "Wukong")
        self.assertIsNone(split_mode_entity_alias("Emerald_Wukong"))


if __name__ == "__main__":
    unittest.main()
