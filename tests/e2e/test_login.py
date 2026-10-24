"""
End-to-end tests for login functionality.
Simulates real user login flows.
"""


class TestLoginFlow:
    """Test the complete login user flow."""

    def test_login_page_loads(self, page):
        """Test that the login page loads correctly."""
        page.goto("/login/")
        assert (
            "Login" in page.title()
            or "Sign In" in page.title()
            or "portfolio" in page.title().lower()
        )

    def test_login_with_valid_credentials(self, page, test_user):
        """Test successful login with valid credentials."""
        page.goto("/login/")

        # Fill in credentials
        page.fill('input[name="username"]', test_user.username)
        page.fill('input[name="password"]', "e2e-pass-123")

        # Submit form
        page.click('button[type="submit"]')
        page.wait_for_url("**/dashboard/**")

        # Verify successful login
        assert "dashboard" in page.url.lower() or "welcome" in page.title().lower()

    def test_login_with_invalid_credentials(self, page):
        """Test login failure with invalid credentials."""
        page.goto("/login/")

        # Fill invalid credentials
        page.fill('input[name="username"]', "invaliduser")
        page.fill('input[name="password"]', "wrongpass")

        # Submit form
        page.click('button[type="submit"]')
        page.wait_for_url("/login/")

        # Verify error message
        assert (
            "invalid" in page.text_content("div").lower()
            or "error" in page.text_content("div").lower()
        )

    def test_login_form_validation(self, page):
        """Test that login form validates empty fields."""
        page.goto("/login/")

        # Try to submit without filling anything
        page.click('button[type="submit"]')

        # Should still be on login page
        assert "/login/" in page.url
