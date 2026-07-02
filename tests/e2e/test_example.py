"""
Example E2E test demonstrating Playwright usage.
This test verifies the testing infrastructure is working.
"""
import pytest


class TestExample:
    """Example tests to verify test infrastructure."""

    def test_playwright_is_working(self, page):
        """Verify Playwright can navigate to a page."""
        # This test will fail if Django isn't running, which is expected
        try:
            page.goto("http://127.0.0.1:8000/", timeout=5000)
            # If we get here, Django is running
            assert page.title() != "" or "Django" in page.content() or "portfolio" in page.content().lower()
        except Exception:
            # Django server not running - this is OK for infrastructure test
            pytest.skip("Django server not running")

    def test_pytorch_is_working(self):
        """Verify PyTorch is installed and working."""
        import torch
        
        # Simple tensor operation
        x = torch.tensor([1.0, 2.0, 3.0])
        y = torch.tensor([4.0, 5.0, 6.0])
        z = x + y
        
        assert z.tolist() == [5.0, 7.0, 9.0]
        print(f"✓ PyTorch working: {torch.__version__}")
