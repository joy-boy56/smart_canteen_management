// Admin slots management functionality
document.addEventListener('DOMContentLoaded', () => {
    // Check if user is admin
    const token = localStorage.getItem('smart_canteen_token');
    if (!token) {
        window.location.href = '../login.html';
        return;
    }

    // Load slots on initial load
    loadSlots();

    // Setup event listeners
    document.getElementById('add-slot-btn').addEventListener('click', () => {
        document.getElementById('slot-form-container').style.display = 'block';
    });

    document.getElementById('close-form-btn').addEventListener('click', () => {
        document.getElementById('slot-form-container').style.display = 'none';
        clearSlotForm();
    });

    document.getElementById('save-slot-btn').addEventListener('click', saveSlot);

    document.getElementById('logout-btn').addEventListener('click', () => {
        localStorage.removeItem('smart_canteen_token');
        window.location.href = '../login.html';
    });
});

// API base URL and auth headers
const API_BASE_URL = 'http://localhost:8000/api';

function getAuthHeaders() {
    const token = localStorage.getItem('smart_canteen_token');
    return token ? { 'Authorization': `Bearer ${token}` } : {};
}

// Load all pickup slots
async function loadSlots() {
    const slotsListContainer = document.getElementById('slots-list');
    if (!slotsListContainer) return;

    try {
        const response = await fetch(`${API_BASE_URL}/pickup-slots/`, {
            headers: {
                ...getAuthHeaders(),
                'Content-Type': 'application/json',
            }
        });

        if (!response.ok) {
            throw new Error('Failed to load pickup slots');
        }

        const slots = await response.json();

        if (slots.length === 0) {
            slotsListContainer.innerHTML = '<p class="empty-slots">No pickup slots found</p>';
            return;
        }

        // Create slots list
        slotsListContainer.innerHTML = '';
        slots.forEach(slot => {
            // Format times for display
            const startTime = new Date(slot.start_time).toLocaleString();
            const endTime = new Date(slot.end_time).toLocaleString();
            const cutoffTime = slot.booking_cutoff ? new Date(slot.booking_cutoff).toLocaleString() : 'Not set';

            // Calculate capacity info
            const effectiveCapacity = Math.floor(slot.max_orders * slot.safety_factor);
            const utilization = (slot.current_orders / slot.max_orders * 100).toFixed(1);
            const remainingCapacity = Math.max(0, effectiveCapacity - slot.current_orders);
            const isFull = slot.current_orders >= effectiveCapacity;

            const slotElement = document.createElement('div');
            slotElement.className = `slot-item ${isFull ? 'slot-full' : ''}`;
            slotElement.innerHTML = `
                <div class="slot-header">
                    <h4>${slot.description || `Slot ${slot.id}`}</h4>
                    <span class="slot-status ${slot.status}">${slot.status.toUpperCase()}</span>
                </div>
                <div class="slot-details">
                    <p><strong>Time:</strong> ${startTime} - ${endTime}</p>
                    <p><strong>Booking Cutoff:</strong> ${cutoffTime}</p>
                    <p><strong>Capacity:</strong> ${slot.current_orders} / ${slot.max_orders} orders</p>
                    <p><strong>Effective Capacity:</strong> ${effectiveCapacity} orders (safety factor: ${slot.safety_factor})</p>
                    <p><strong>Remaining Capacity:</strong> <span class="${remainingCapacity > 0 ? 'available' : 'full'}">${remainingCapacity}</span></p>
                    <p><strong>Utilization:</strong> ${utilization}%</p>
                </div>
                <div class="slot-actions">
                    <button class="btn btn-sm btn-outline edit-slot" data-slot-id="${slot.id}">Edit</button>
                    <button class="btn btn-sm btn-outline delete-slot" data-slot-id="${slot.id}">Delete</button>
                </div>
            `;
            slotsListContainer.appendChild(slotElement);
        });

        // Add event listeners to edit and delete buttons
        document.querySelectorAll('.edit-slot').forEach(button => {
            button.addEventListener('click', (e) => {
                const slotId = parseInt(e.target.dataset.slotId);
                editSlot(slotId);
            });
        });

        document.querySelectorAll('.delete-slot').forEach(button => {
            button.addEventListener('click', (e) => {
                const slotId = parseInt(e.target.dataset.slotId);
                deleteSlot(slotId);
            });
        });

    } catch (error) {
        console.error('Error loading slots:', error);
        slotsListContainer.innerHTML = `<p class="error">Failed to load pickup slots. Please try again later.</p>`;
    }
}

// Clear the slot form
function clearSlotForm() {
    document.getElementById('slot-start-time').value = '';
    document.getElementById('slot-end-time').value = '';
    document.getElementById('slot-booking-cutoff').value = '';
    document.getElementById('slot-max-orders').value = '20';
    document.getElementById('slot-safety-factor').value = '0.8';
    document.getElementById('slot-description').value = '';
    document.getElementById('slot-status').value = 'active';
}

// Save a new slot or update an existing one
async function saveSlot() {
    const startTimeEl = document.getElementById('slot-start-time');
    const endTimeEl = document.getElementById('slot-end-time');
    const bookingCutoffEl = document.getElementById('slot-booking-cutoff');
    const maxOrdersEl = document.getElementById('slot-max-orders');
    const safetyFactorEl = document.getElementById('slot-safety-factor');
    const descriptionEl = document.getElementById('slot-description');
    const statusEl = document.getElementById('slot-status');

    const startTime = startTimeEl.value;
    const endTime = endTimeEl.value;
    const bookingCutoff = bookingCutoffEl.value || null;
    const maxOrders = parseInt(maxOrdersEl.value);
    const safetyFactor = parseFloat(safetyFactorEl.value);
    const description = descriptionEl.value || null;
    const status = statusEl.value;

    // Basic validation
    if (!startTime || !endTime) {
        alert('Please fill in start and end times');
        return;
    }

    if (new Date(endTime) <= new Date(startTime)) {
        alert('End time must be after start time');
        return;
    }

    if (bookingCutoff && new Date(bookingCutoff) >= new Date(startTime)) {
        alert('Booking cutoff must be before slot start time');
        return;
    }

    if (maxOrders <= 0) {
        alert('Maximum orders must be greater than 0');
        return;
    }

    if (safetyFactor < 0 || safetyFactor > 1) {
        alert('Safety factor must be between 0 and 1');
        return;
    }

    // Show loading state
    const saveBtn = document.getElementById('save-slot-btn');
    const originalText = saveBtn.textContent;
    saveBtn.textContent = 'Saving...';
    saveBtn.disabled = true;

    try {
        const slotData = {
            start_time: startTime,
            end_time: endTime,
            booking_cutoff: bookingCutoff,
            max_orders: maxOrders,
            safety_factor: safetyFactor,
            description: description,
            status: status
        };

        const response = await fetch(`${API_BASE_URL}/pickup-slots/`, {
            method: 'POST',
            headers: {
                ...getAuthHeaders(),
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(slotData)
        });

        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || 'Failed to save slot');
        }

        // Hide form and reset
        document.getElementById('slot-form-container').style.display = 'none';
        clearSlotForm();

        // Reload slots
        loadSlots();

        alert('Slot saved successfully!');
    } catch (error) {
        console.error('Error saving slot:', error);
        alert('Failed to save slot: ' + error.message);
    } finally {
        saveBtn.textContent = originalText;
        saveBtn.disabled = false;
    }
}

// Edit an existing slot (placeholder for future implementation)
async function editSlot(slotId) {
    alert('Edit functionality coming soon!');
    // In a full implementation, this would:
    // 1. Fetch the slot data by ID
    // 2. Populate the form with the slot data
    // 3. Change the save button to update instead of create
    // 4. Show the form
}

// Delete a slot
async function deleteSlot(slotId) {
    if (!confirm('Are you sure you want to delete this slot? This cannot be undone.')) {
        return;
    }

    try {
        const response = await fetch(`${API_BASE_URL}/pickup-slots/${slotId}`, {
            method: 'DELETE',
            headers: {
                ...getAuthHeaders(),
                'Content-Type': 'application/json',
            }
        });

        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || 'Failed to delete slot');
        }

        loadSlots();
        alert('Slot deleted successfully!');
    } catch (error) {
        console.error('Error deleting slot:', error);
        alert('Failed to delete slot: ' + error.message);
    }
}