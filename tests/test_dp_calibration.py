import unittest
from decimal import Decimal

from ProgramFiles.dp_calibration import (
    calibrate_dp,
    cdf_thresholds,
    grid_tv_bound,
    ideal_dp_k,
    implemented_mass_counts,
    implemented_tv_distance,
)


class DPCalibrationTests(unittest.TestCase):
    def test_thesis_grid_keeps_existing_k_values(self):
        expected = {
            Decimal("0.1"): [18, 40, 63, 86],
            Decimal("0.5"): [7, 12, 16, 21],
            Decimal("1.0"): [4, 7, 9, 11],
            Decimal("2.0"): [3, 4, 5, 6],
        }
        deltas = [Decimal("1e-2"), Decimal("1e-3"), Decimal("1e-4"), Decimal("1e-5")]
        for epsilon, ks in expected.items():
            for delta, k in zip(deltas, ks):
                with self.subTest(epsilon=epsilon, delta=delta):
                    calibration = calibrate_dp(epsilon, delta)
                    self.assertEqual(calibration.k, k)
                    self.assertEqual(ideal_dp_k(epsilon, delta), k)

    def test_reserved_budget_meets_requested_delta(self):
        for epsilon in map(Decimal, ("0.1", "0.5", "1", "2")):
            for delta in map(Decimal, ("1e-2", "1e-3", "1e-4", "1e-5")):
                with self.subTest(epsilon=epsilon, delta=delta):
                    c = calibrate_dp(epsilon, delta)
                    spent = c.ideal_delta + c.grid_delta_reserve
                    self.assertLessEqual(spent, c.requested_delta)
                    self.assertEqual(c.grid_tv_bound, grid_tv_bound(c.k))

    def test_fixed_point_recalibrates_when_grid_reserve_crosses_boundary(self):
        epsilon = Decimal("0.1")
        delta = Decimal("1e-6")
        self.assertEqual(ideal_dp_k(epsilon, delta), 109)
        calibration = calibrate_dp(epsilon, delta)
        self.assertEqual(calibration.k, 110)
        self.assertEqual(ideal_dp_k(epsilon, calibration.ideal_delta), 110)

    def test_rejects_delta_smaller_than_grid_reserve(self):
        with self.assertRaisesRegex(ValueError, "too small"):
            calibrate_dp(Decimal("1"), Decimal("1e-20"))

    def test_integer_mass_table_is_an_exact_distribution(self):
        thresholds = cdf_thresholds(Decimal("1"), 4)
        masses = implemented_mass_counts(thresholds)
        self.assertEqual(len(thresholds), 8)
        self.assertEqual(len(masses), 9)
        self.assertEqual(sum(masses), 2 ** 32)
        self.assertTrue(all(mass > 0 for mass in masses))
        self.assertEqual(masses, list(reversed(masses)))

    def test_measured_tv_is_below_calibration_bound(self):
        for epsilon in map(Decimal, ("0.1", "0.5", "1", "2")):
            for delta in map(Decimal, ("1e-2", "1e-3", "1e-4", "1e-5")):
                k = calibrate_dp(epsilon, delta).k
                with self.subTest(epsilon=epsilon, delta=delta, k=k):
                    self.assertLessEqual(implemented_tv_distance(epsilon, k), grid_tv_bound(k))

    def test_all_e8_grid_thresholds_are_strictly_monotone(self):
        for epsilon in map(Decimal, ("0.1", "0.5", "1", "2")):
            for delta in map(Decimal, ("1e-2", "1e-3", "1e-4", "1e-5")):
                k = calibrate_dp(epsilon, delta).k
                thresholds = cdf_thresholds(epsilon, k)
                with self.subTest(epsilon=epsilon, delta=delta, k=k):
                    self.assertTrue(all(a < b for a, b in zip(thresholds, thresholds[1:])))


if __name__ == "__main__":
    unittest.main()
