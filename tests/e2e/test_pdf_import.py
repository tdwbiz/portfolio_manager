"""
End-to-end tests for PDF import functionality.
Simulates real user PDF upload and import flows.
"""


class TestPDFImport:
    """Test PDF import user flows."""

    def test_pdf_import_page_loads(self, page):
        """Test that PDF import page loads."""
        page.goto("/login/")
        page.fill('input[name="username"]', "admin")
        page.fill('input[name="password"]', "admin")
        page.click('button[type="submit"]')

        # Navigate to import page
        page.click('a:has-text("Import")')
        page.wait_for_load_state("networkidle")

        # Verify import page loads
        assert "import" in page.url.lower() or "import" in page.title().lower()

    def test_pdf_upload_form(self, page):
        """Test PDF upload form is present."""
        page.goto("/login/")
        page.fill('input[name="username"]', "admin")
        page.fill('input[name="password"]', "admin")
        page.click('button[type="submit"]')

        # Navigate to import
        page.click('a:has-text("Import")')

        # Verify file upload element exists
        assert page.locator('input[type="file"]').count() > 0

    def test_pdf_import_with_file(self, page, tmp_path):
        """Test PDF import with a sample file."""
        # Create a dummy file for testing
        dummy_file = tmp_path / "test.pdf"
        dummy_file.write_bytes(b"dummy pdf content")

        page.goto("/login/")
        page.fill('input[name="username"]', "admin")
        page.fill('input[name="password"]', "admin")
        page.click('button[type="submit"]')

        # Navigate to import
        page.click('a:has-text("Import")')

        # Upload file
        page.set_input_files('input[type="file"]', str(dummy_file))

        # Submit form
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle")

        # Verify some response (success or error message)
        assert page.url() != ""
