// Cart page functionality
document.addEventListener('DOMContentLoaded', () => {
    loadCart();
    loadAvailableSlots();
    setupEventListeners();
});

// API base URL and auth headers
const API_BASE_URL = 'http://localhost:8000/api';

function getAuthHeaders() {
    const token = localStorage.getItem('smart_canteen_token');
    return token ? { 'Authorization': `Bearer ${token}` } : {};
}

// Load available pickup slots
async function loadAvailableSlots() {
    const slotSelectionContainer = document.getElementById('slot-selection');
    if (!slotSelectionContainer) return;

    try {
        const response = await fetch(`${API_BASE_URL}/pickup-slots/active/`, {
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
            slotSelectionContainer.innerHTML = '<p class="empty-slots">No pickup slots available</p>';
            return;
        }

        // Create slot selection radio buttons
        slotSelectionContainer.innerHTML = '';
        slots.forEach(slot => {
            // Format times for display
            const startTime = new Date(slot.start_time).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
            const endTime = new Date(slot.end_time).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});

            // Calculate remaining capacity
            const effectiveCapacity = Math.floor(slot.max_orders * slot.safety_factor);
            const remainingCapacity = Math.max(0, effectiveCapacity - slot.current_orders);
            const isFull = slot.current_orders >= effectiveCapacity;

            const slotElement = document.createElement('div');
            slotElement.className = `slot-option ${isFull ? 'slot-full' : ''}`;
            slotElement.innerHTML = `
                <div class="slot-header">
                    <input type="radio" id="slot-${slot.id}" name="pickup_slot_id" value="${slot.id}" ${isFull ? 'disabled' : ''}>
                    <label for="slot-${slot.id}">${startTime} - ${endTime}</label>
                </div>
                <div class="slot-details">
                    <span class="slot-capacity">${remainingCapacity} / ${effectiveCapacity} orders available</span>
                    ${slot.description ? `<span class="slot-description"> - ${slot.description}</span>` : ''}
                </div>
            `;
            slotSelectionContainer.appendChild(slotElement);
        });

    } catch (error) {
        console.error('Error loading slots:', error);
        slotSelectionContainer.innerHTML = `<p class="error">Failed to load pickup slots. Please try again later.</p>`;
    }
}

function loadCart() {
    const cartItemsContainer = document.getElementById('cart-items');
    const cartSummaryContainer = document.getElementById('cart-summary');
    if (!cartItemsContainer || !cartSummaryContainer) return;

    const cart = getCart();

    if (cart.length === 0) {
        cartItemsContainer.innerHTML = `
            <div class="empty-cart">
                <p>Your cart is empty</p>
                <a href="menu.html" class="btn btn-primary">Browse Menu</a>
            </div>
        `;
        cartSummaryContainer.innerHTML = '';
        return;
    }

    // Display cart items
    cartItemsContainer.innerHTML = '';
    cart.forEach((item, index) => {
        const itemElement = document.createElement('div');
        itemElement.className = 'cart-item';
        itemElement.innerHTML = `
            <div class="item-info">
                <h4>${item.itemName}</h4>
                <p>₹${(item.itemPrice / 100).toFixed(2)} each</p>
            </div>
            <div class="item-controls">
                <button class="btn btn-sm btn-outline decrease" data-index="${index}">−</button>
                <span class="quantity">${item.quantity}</span>
                <button class="btn btn-sm btn-outline increase" data-index="${index}">+</button>
                <button class="btn btn-sm btn-danger remove" data-index="${index}">Remove</button>
            </div>
            <div class="item-total">
                ₹${((item.itemPrice * item.quantity) / 100).toFixed(2)}
            </div>
        `;
        cartItemsContainer.appendChild(itemElement);
    });

    // Update summary
    updateCartSummary();

    // Add event listeners to controls
    document.querySelectorAll('.decrease').forEach(button => {
        button.addEventListener('click', (e) => {
            const index = parseInt(e.target.dataset.index);
            decreaseQuantity(index);
        });
    });

    document.querySelectorAll('.increase').forEach(button => {
        button.addEventListener('click', (e) => {
            const index = parseInt(e.target.dataset.index);
            increaseQuantity(index);
        });
    });

    document.querySelectorAll('.remove').forEach(button => {
        button.addEventListener('click', (e) => {
            const index = parseInt(e.target.dataset.index);
            removeItem(index);
        });
    });
}

function updateCartSummary() {
    const cart = getCart();
    const cartSummaryContainer = document.getElementById('cart-summary');
    if (!cartSummaryContainer) return;

    if (cart.length === 0) {
        cartSummaryContainer.innerHTML = '';
        return;
    }

    const subtotal = cart.reduce((sum, item) => sum + (item.itemPrice * item.quantity), 0);
    // In a real app, we might add taxes, delivery fee, etc.
    const total = subtotal; // For now, no additional fees

    cartSummaryContainer.innerHTML = `
        <div class="summary-row">
            <span>Subtotal:</span>
            <span>₹${(subtotal / 100).toFixed(2)}</span>
        </div>
        <div class="summary-row total">
            <span>Total:</span>
            <span>₹${(total / 100).toFixed(2)}</span>
        </div>
    `;
}

function decreaseQuantity(index) {
    const cart = getCart();
    if (cart[index].quantity > 1) {
        cart[index].quantity -= 1;
    } else {
        // If quantity is 1, removing might be better, but we'll just keep at 1 for simplicity
        // Or we could remove the item if quantity becomes 0 after decrease
        // Let's remove if quantity becomes 0
        if (cart[index].quantity === 1) {
            removeItem(index);
            return;
        }
    }
    saveCart(cart);
    loadCart(); // Reload to update UI
    updateCartCountInHeader();
}

function increaseQuantity(index) {
    const cart = getCart();
    cart[index].quantity += 1;
    saveCart(cart);
    loadCart(); // Reload to update UI
    updateCartCountInHeader();
}

function removeItem(index) {
    const cart = getCart();
    cart.splice(index, 1);
    saveCart(cart);
    loadCart(); // Reload to update UI
    updateCartCountInHeader();
}

function updateCartCountInHeader() {
    const cartCountEl = document.querySelector('#cart-count');
    if (cartCountEl) {
        const cart = getCart();
        const totalItems = cart.reduce((sum, item) => sum + item.quantity, 0);
        cartCountEl.textContent = totalItems;
    }
}

function setupEventListeners() {
    const checkoutBtn = document.getElementById('checkout-btn');
    if (checkoutBtn) {
        checkoutBtn.addEventListener('click', handleCheckout);
    }

    // Also update cart count in header on load (in case we came from another page)
    updateCartCountInHeader();
}

async function handleCheckout() {
    // Get selected pickup slot
    const selectedSlotEl = document.querySelector('input[name="pickup_slot_id"]:checked');
    const pickupSlotId = selectedSlotEl ? parseInt(selectedSlotEl.value) : null;

    // Get payment method
    const paymentMethodEl = document.querySelector('input[name="payment_method"]:checked');
    const paymentMethod = paymentMethodEl ? paymentMethodEl.value : 'cash';
    // For online payments, start with unpaid status and require payment confirmation
    // For cash payments, we'll use unpaid (to be paid at canteen) - could also be 'paid' if considering immediate payment
    const paymentStatus = 'unpaid'; // All orders start as unpaid; payment status updated after confirmation

    const cart = getCart();
    if (cart.length === 0) {
        alert('Your cart is empty');
        return;
    }

    // Prepare order items for API
    const orderItems = cart.map(item => ({
        menu_item_id: item.itemId,
        quantity: item.quantity
    }));

    // Show loading state
    const checkoutBtn = document.getElementById('checkout-btn');
    const originalText = checkoutBtn.textContent;
    checkoutBtn.textContent = 'Placing order...';
    checkoutBtn.disabled = true;

    try {
        const response = await fetch(`${API_BASE_URL}/orders/`, {
            method: 'POST',
            headers: {
                ...getAuthHeaders(),
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                items: orderItems,
                pickup_slot_id: pickupSlotId,
                payment_method: paymentMethod,
                payment_status: paymentStatus
            })
        });

        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || 'Failed to place order');
        }

        const orderData = await response.json();
        // Redirect to order confirmation page with order ID
        window.location.href = `order-confirmation.html?order_id=${orderData.id}`;
    } catch (error) {
        console.error('Error placing order:', error);
        alert('Failed to place order: ' + error.message);
    } finally {
        checkoutBtn.textContent = originalText;
        checkoutBtn.disabled = false;
    }
}

// Cart functions using localStorage (same as in menu.js)
const CART_KEY = 'smart_canteen_cart';
const API_BASE_URL = 'http://localhost:8000/api';

function getCart() {
    const cart = localStorage.getItem(CART_KEY);
    return cart ? JSON.parse(cart) : [];
}

function saveCart(cart) {
    localStorage.setItem(CART_KEY, JSON.stringify(cart));
}

function getAuthHeaders() {
    const token = localStorage.getItem('smart_canteen_token');
    return token ? { 'Authorization': `Bearer ${token}` } : {};
}

// Update cart count in header (same as in menu.js)
function updateCartCountInHeader() {
    const cartCountEl = document.querySelector('#cart-count');
    if (cartCountEl) {
        const cart = getCart();
        const totalItems = cart.reduce((sum, item) => sum + item.quantity, 0);
        cartCountEl.textContent = totalItems;
    }
}