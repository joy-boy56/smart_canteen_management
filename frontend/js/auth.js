// Auth API base URL
const API_BASE_URL = 'http://localhost:8000/api';

// Token key for localStorage
const TOKEN_KEY = 'smart_canteen_token';

// Set auth header for requests
function getAuthHeaders() {
    const token = localStorage.getItem(TOKEN_KEY);
    return token ? { 'Authorization': `Bearer ${token}` } : {};
}

// Login function
async function login(email, password) {
    try {
        const response = await fetch(`${API_BASE_URL}/auth/login`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            body: new URLSearchParams({
                'username': email,
                'password': password
            })
        });

        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || 'Login failed');
        }

        const data = await response.json();
        localStorage.setItem(TOKEN_KEY, data.access_token);
        return data;
    } catch (error) {
        throw error;
    }
}

// Register function
async function register(userData) {
    try {
        const response = await fetch(`${API_BASE_URL}/auth/register`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(userData)
        });

        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || 'Registration failed');
        }

        const data = await response.json();
        return data;
    } catch (error) {
        throw error;
    }
}

// Get current user
async function getCurrentUser() {
    try {
        const response = await fetch(`${API_BASE_URL}/auth/me`, {
            method: 'GET',
            headers: {
                ...getAuthHeaders(),
                'Content-Type': 'application/json',
            }
        });

        if (!response.ok) {
            throw new Error('Failed to fetch user');
        }

        return await response.json();
    } catch (error) {
        throw error;
    }
}

// Logout function
function logout() {
    localStorage.removeItem(TOKEN_KEY);
    window.location.href = 'login.html';
}

// Check if user is logged in
function isLoggedIn() {
    return !!localStorage.getItem(TOKEN_KEY);
}

// Export functions for use in other files
window.auth = { login, register, getCurrentUser, logout, isLoggedIn };