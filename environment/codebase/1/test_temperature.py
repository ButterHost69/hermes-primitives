import unittest

from temperature import celsius_to_fahrenheit, fahrenheit_to_celsius


class TestTemperature(unittest.TestCase):
    def test_freezing_point(self):
        self.assertEqual(celsius_to_fahrenheit(0), 32)

    def test_boiling_point(self):
        self.assertEqual(celsius_to_fahrenheit(100), 212)

    def test_roundtrip(self):
        self.assertAlmostEqual(
            fahrenheit_to_celsius(celsius_to_fahrenheit(37)), 37, places=6
        )


if __name__ == "__main__":
    unittest.main()
