import unittest

from core import scaled_purchase_quantity, normalise_item


class ScalingTests(unittest.TestCase):
    def test_scales_loose_ingredients_from_four_to_six_servings(self):
        item = {"name": "Tomatoes", "quantity": 2, "unit": "each"}
        self.assertEqual(scaled_purchase_quantity(item, servings=6, base_servings=4), (3, "each"))

    def test_rounds_packages_up_to_a_whole_pack(self):
        item = {
            "name": "Chickpea pasta", "quantity": 300, "unit": "g",
            "package_size": 250, "package_unit": "g", "purchase_unit": "pack",
        }
        self.assertEqual(scaled_purchase_quantity(item, servings=4, base_servings=4), (2, "pack"))

    def test_keeps_bunches_as_whole_bunches_when_scaled(self):
        item = {"name": "Coriander", "quantity": 1, "unit": "bunch"}
        self.assertEqual(scaled_purchase_quantity(item, servings=6, base_servings=4), (2, "bunch"))

    def test_rounds_loose_heads_and_bulbs_up(self):
        self.assertEqual(scaled_purchase_quantity({"name": "Garlic", "quantity": 2, "unit": "bulb"}, servings=5), (3, "bulb"))
        self.assertEqual(scaled_purchase_quantity({"name": "Broccoli", "quantity": 1, "unit": "head"}, servings=5), (2, "head"))

    def test_normalises_a_new_item_with_a_default_aisle(self):
        item = normalise_item({"name": "Milk", "quantity": 2, "unit": "L"})
        self.assertEqual(item["aisle"], "Dairy & fridge")
        self.assertFalse(item["checked"])


if __name__ == "__main__":
    unittest.main()
