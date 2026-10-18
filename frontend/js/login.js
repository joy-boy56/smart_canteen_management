// Handle login form submission
document.addEventListener('DOMContentLoaded', () => {
    const loginForm = document.getElementById('login-form');
    if (!loginForm) return;

    loginForm.addEventListener('async', async (e) => {
        e.preventDefault();

        const email = document.getElementById('email').value;
        const password = document.getElementById('password').value;

        // Show loading state
        const submitButton = loginForm.querySelector('button[type="submit"]');
        const originalText = submitButton.textContent;
        submitButton.textContent = 'Logging in...';
        submitButton.disabled = true;

        try {
            await window.auth.login(email, password);
            // After login, fetch user to determine role
            const user = await window.auth.getCurrentUser();
            // Redirect based on role
            if (user.role === 'admin') {
                window.location.href = 'admin/dashboard.html';
            } else {
                window.location.href = 'student/dashboard.html';
            }
        } catch (error) {
            alert('Login failed: ' + error.message);
            console.error(error);
        } finally {
            submitButton.textContent = originalText;
            submitButton.disabled = false;
        }
    });
});