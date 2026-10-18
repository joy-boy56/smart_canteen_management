// Menu page functionality
document.addEventListener('DOMContentLoaded', async () => {
    // Load menu items
    await loadMenuItems();
    // Update cart count display
    updateCartCount();
});

async function loadMenuItems() {
    const menuGrid = document.getElementById('menu-items');
    if (!menuGrid) return;

    try {
        const response = await fetch(`${API_BASE_URL}/menu/`, {
            headers: {
                ...getAuthHeaders(),
                'Content-Type': 'application/json',
            }
        });

        if (!response.ok) {
            throw new Error('Failed to load menu');
        }

        const menuItems = await response.json();

        // Clear loading message
        menuGrid.innerHTML = '';

        if (menuItems.length === 0) {
            menuGrid.innerHTML = '<p class="empty-menu">No menu items available</p>';
            return;
        }

        // Create menu item cards
        menuItems.forEach(item => {
            const itemCard = document.createElement('div');
            itemCard.className = 'menu-item-card';
            itemCard.innerHTML = `
                <h3>${item.name}</h3>
                <p class="description">${item.description || ''}</p>
                <p class="price">₹${(item.price / 100).toFixed(2)}</p>
                ${item.preparation_time_estimate ? `<p class="prep-time">Prep time: ~${item.preparation_time_estimate} min</p>` : ''}
                <button class="btn btn-primary add-to-cart" data-item-id="${item.id}" data-item-name="${item.name}" data-item-price="${item.price}">
                    Add to Cart
                </button>
            `;
            menuGrid.appendChild(itemCard);
        });

        // Add event listeners to add to cart buttons
        document.querySelectorAll('.add-to-cart').forEach(button => {
            button.addEventListener('click', (e) => {
                const itemId = parseInt(e.target.dataset.itemId);
                const itemName = e.target.dataset.itemName;
                const itemPrice = parseInt(e.target.dataset.itemPrice);
                addToCart({ itemId, itemName, itemPrice });
                updateCartCount();
                showFeedback(`${itemName} added to cart!`);
            });
        });

    } catch (error) {
        console.error('Error loading menu:', error);
        menuGrid.innerHTML = `<p class="error">Failed to load menu. Please try again later.</p>`;
    }
}

// Cart functions using localStorage
const CART_KEY = 'smart_canteen_cart';

function getCart() {
    const cart = localStorage.getItem(CART_KEY);
    return cart ? JSON.parse(cart) : [];
}

function saveCart(cart) {
    localStorage.setItem(CART_KEY, JSON.stringify(cart));
}

function addToCart(item) {
    const cart = getCart();
    const existingItem = cart.find(i => i.itemId === item.itemId);
    if (existingItem) {
        existingItem.quantity += 1;
    } else {
        cart.push({
            itemId: item.itemId,
            itemName: item.itemName,
            itemPrice: item.itemPrice,
            quantity: 1
        });
    }
    saveCart(cart);
}

function updateCartCount() {
    const cartCountEl = document.getElementById('cart-count');
    if (cartCountEl) {
        const cart = getCart();
        const totalItems = cart.reduce((sum, item) => sum + item.quantity, 0);
        cartCountEl.textContent = totalItems;
    }
}

function showFeedback(message) {
    // Create a temporary feedback element
    const feedback = document.createElement('div');
    feedback.className = 'feedback';
    feedback.textContent = message;
    feedback.style.position = 'fixed';
    feedback.style.bottom = '20px';
    feedback.style.left = '50%';
    feedback.style.transform = 'translateX(-50%)';
    feedback.style.background = '#27ae60';
    feedback.style.color = 'white';
    feedback.style.padding = '10px 20px';
    feedback.style.borderRadius = '4px';
    feedback.style.zIndex = '1000';
    document.body.appendChild(feedback);

    // Remove after 2 seconds
    setTimeout(() => {
        feedback.remove();
    }, 2000);
}

// API base URL and auth headers (copied from auth.js for menu.js independence)
const API_BASE_URL = 'http://localhost:8000/api';

function getAuthHeaders() {
    const token = localStorage.getItem('smart_canteen_token');
    return token ? { 'Authorization': `Bearer ${token}` } : {};
}