// Dashboard common functionality
document.addEventListener('DOMContentLoaded', async () => {
    // Handle logout button
    const logoutBtn = document.getElementById('logout-btn');
    if (logoutBtn) {
        logoutBtn.addEventListener('click', () => {
            window.auth.logout();
        });
    }

    // For student dashboard, load and display user name
    const userNameElement = document.getElementById('user-name');
    if (userNameElement) {
        try {
            const user = await window.auth.getCurrentUser();
            // Display full name or email if full name not available
            userNameElement.textContent = user.full_name || user.email || 'User';
        } catch (error) {
            console.error('Failed to fetch user info:', error);
            userNameElement.textContent = 'User';
            // Optionally redirect to login if not authenticated
            // window.location.href = '../login.html';
        }
    }
});