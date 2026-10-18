// Handle register form submission
document.addEventListener('DOMContentLoaded', () => {
    const registerForm = document.getElementById('register-form');
    if (!registerForm) return;

    registerForm.addEventListener('submit', async (e) => {
        e.preventDefault();

        const full_name = document.getElementById('full_name').value;
        const email = document.getElementById('email').value;
        const phone_number = document.getElementById('phone_number').value;
        const student_reg_number = document.getElementById('student_reg_number').value;
        const department = document.getElementById('department').value;
        const year_semester = document.getElementById('year_semester').value;
        const password = document.getElementById('password').value;

        const userData = {
            full_name,
            email,
            phone_number,
            student_reg_number: student_reg_number || null,
            department: department || null,
            year_semester: year_semester || null,
            password
        };

        // Show loading state
        const submitButton = registerForm.querySelector('button[type="submit"]');
        const originalText = submitButton.textContent;
        submitButton.textContent = 'Registering...';
        submitButton.disabled = true;

        try {
            await window.auth.register(userData);
            alert('Registration successful! Please login.');
            window.location.href = 'login.html';
        } catch (error) {
            alert('Registration failed: ' + error.message);
            console.error(error);
        } finally {
            submitButton.textContent = originalText;
            submitButton.disabled = false;
        }
    });
});