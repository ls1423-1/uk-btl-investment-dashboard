from __future__ import annotations

import unittest

from btl_core import build_investment_projection, monthly_mortgage_payment, mortgage_amortisation


class InvestmentCalculatorTests(unittest.TestCase):
    def test_repayment_and_interest_only_payments(self) -> None:
        self.assertAlmostEqual(monthly_mortgage_payment(200_000, 5.0, 25, "Repayment"), 1169.18, places=2)
        self.assertAlmostEqual(monthly_mortgage_payment(200_000, 5.0, 25, "Interest-only"), 833.33, places=2)

    def test_repayment_mortgage_reduces_balance(self) -> None:
        schedule = mortgage_amortisation(150_000, 5.0, 25, 60, "Repayment")
        self.assertEqual(len(schedule), 60)
        self.assertLess(schedule.iloc[-1]["balance"], 150_000)
        self.assertGreater(schedule["principal_paid"].sum(), 0)

    def test_unlevered_one_year_roi(self) -> None:
        results, projection = build_investment_projection(
            purchase_price=100_000,
            deposit=100_000,
            monthly_rent=1_000,
            holding_years=1,
            property_growth_pct=0,
            rent_growth_pct=0,
            mortgage_rate_pct=5,
            mortgage_term_years=25,
            repayment_type="Repayment",
            void_pct=0,
            operating_cost_pct=0,
            fixed_annual_costs=0,
            purchase_costs=0,
            selling_cost_pct=0,
        )
        self.assertAlmostEqual(results["gross_yield"], 12.0)
        self.assertAlmostEqual(results["net_operating_yield"], 12.0)
        self.assertAlmostEqual(results["total_profit"], 12_000)
        self.assertAlmostEqual(results["total_roi"], 12.0)
        self.assertAlmostEqual(results["annualised_roi"], 12.0)
        self.assertEqual(float(projection.iloc[-1]["mortgage_balance"]), 0.0)


if __name__ == "__main__":
    unittest.main()
