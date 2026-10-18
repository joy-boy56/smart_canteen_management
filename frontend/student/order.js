// Order confirmation page functionality
document.addEventListener('DOMContentLoaded', async () => {
    // Get order ID from query parameters
    const urlParams = new URLSearchParams(window.location.search);
    const orderId = urlParams.get('order_id');
    if (!orderId) {
        window.location.href = 'menu.html';
        return;
    }

    try {
        // Fetch order details
        const orderResponse = await fetch(`${API_BASE_URL}/orders/${orderId}`, {
            headers: {
                ...getAuthHeaders(),
                'Content-Type': 'application/json',
            }
        });

        if (!orderResponse.ok) {
            throw new Error('Failed to fetch order details');
        }

        const order = await orderResponse.json();

        // Display order details
        displayOrderDetails(order);

        // Fetch and display token
        await displayToken(orderId, order);
    } catch (error) {
        console.error('Error loading order confirmation:', error);
        document.getElementById('order-details').innerHTML = `<p class="error">Failed to load order details. Please try again later.</p>`;
    }
});

async function displayOrderDetails(order) {
    const container = document.getElementById('order-details');
    if (!container) return;

    // Format date time
    const formatDate = (dateString) => {
        if (!dateString) return 'Not set';
        return new Date(dateString).toLocaleString();
    };

    // Calculate total from items (or use order.total_amount)
    const total = order.total_amount / 100; // convert from paise to rupees

    // Format pickup slot time if available
    let pickupSlotInfo = '';
    if (order.pickup_slot) {
        const startTime = new Date(order.pickup_slot.start_time).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
        const endTime = new Date(order.pickup_slot.end_time).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
        pickupSlotInfo = `<p><strong>Pickup Slot:</strong> ${startTime} - ${endTime}</p>`;
        if (order.pickup_slot.description) {
            pickupSlotInfo += `<p><strong>Slot Description:</strong> ${order.pickup_slot.description}</p>`;
        }
    } else if (order.requested_pickup_time) {
        pickupSlotInfo = `<p><strong>Requested Pickup Time:</strong> ${formatDate(order.requested_pickup_time)}</p>`;
    }

    // Determine payment status styling
    const paymentStatusClass = order.payment_status === 'paid' ? 'paid' :
                              order.payment_status === 'unpaid' ? 'unpaid' : 'pending';

    container.innerHTML = `
        <div class="order-info">
            <h3>Order #${order.human_readable_id}</h3>
            <p><strong>Status:</strong> ${order.status}</p>
            <p><strong>Payment Method:</strong> ${order.payment_method}</p>
            <p><strong>Payment Status:</strong> <span class="${paymentStatusClass}">${order.payment_status.toUpperCase()}</span></p>
            <p><strong>Order Time:</strong> ${formatDate(order.created_at)}</p>
            ${pickupSlotInfo}
            <p><strong>Total Amount:</strong> ₹${total.toFixed(2)}</p>
        </div>
        <div class="order-items">
            <h4>Items:</h4>
            <ul>
                ${order.items.map(item => `
                    <li>
                        ${item.quantity} × ${item.item_name_snapshot}
                        <span>₹${(item.unit_price_snapshot / 100).toFixed(2)} each</span>
                        <span>₹${(item.subtotal / 100).toFixed(2)} total</span>
                    </li>
                `).join('')}
            </ul>
        </div>
    `;

    // Add payment processing section for online payments
    const paymentActionsContainer = document.createElement('div');
    paymentActionsContainer.className = 'payment-actions';

    if (order.payment_method === 'online' && order.payment_status === 'unpaid') {
        paymentActionsContainer.innerHTML = `
            <div class="payment-section">
                <h3>Payment Required</h3>
                <p>Your order total is ₹${total.toFixed(2)}. Please complete the payment to confirm your order.</p>
                <button id="pay-now-btn" class="btn btn-primary">Pay Now</button>
                <div id="payment-status" class="mt-2"></div>
            </div>
        `;
        container.appendChild(paymentActionsContainer);

        // Add event listener to pay now button
        document.getElementById('pay-now-btn').addEventListener('click', () => {
            processPayment(order.id, order.total_amount);
        });
    } else if (order.payment_method === 'online' && order.payment_status === 'paid') {
        paymentActionsContainer.innerHTML = `
            <div class="payment-section">
                <h3>Payment Successful</h3>
                <p>Your payment of ₹${total.toFixed(2)} has been successfully processed.</p>
            </div>
        `;
        container.appendChild(paymentActionsContainer);
    }
}

async function displayToken(orderId, order) {
    const tokenSection = document.getElementById('token-section');
    if (!tokenSection) return;

    try {
        const tokenResponse = await fetch(`${API_BASE_URL}/tokens/order/${orderId}`, {
            headers: {
                ...getAuthHeaders(),
                'Content-Type': 'application/json',
            }
        });

        let tokenData;
        if (tokenResponse.ok) {
            tokenData = await tokenResponse.json();
        } else {
            // If token endpoint fails (e.g., token not yet generated), generate a demo token
            tokenData = { opaque_token: `DEMO-${Math.random().toString(36).substr(2, 8).toUpperCase()}` };
            console.warn('Token endpoint not available, using demo token');
        }

        const token = tokenData.opaque_token;

        // Determine payment status class for styling
        const paymentStatusClass = order.payment_status === 'paid' ? 'paid' : 'unpaid';

        tokenSection.innerHTML = `
            <h3>Your Order Token</h3>
            <p class="token-value">${token}</p>
            <p class="token-status">Payment Status: <span class="${paymentStatusClass}">${order.payment_status.toUpperCase()}</span></p>
            <p class="token-instructions">Show this token at the counter to collect your order.</p>
            <div id="qrcode"></div>
        `;

        // Generate QR code if the library is available
        if (typeof QRCode !== 'undefined') {
            new QRCode(document.getElementById("qrcode"), {
                text: token,
                width: 128,
                height: 128
            });
        } else {
            // Load QRCode.js dynamically if not already loaded
            const script = document.createElement('script');
            script.src = 'https://cdn.jsdelivr.net/npm/qrcode@1.5.1/build/qrcode.min.js';
            script.onload = () => {
                new QRCode(document.getElementById("qrcode"), {
                    text: token,
                    width: 128,
                    height: 128
                });
            };
            document.head.appendChild(script);
        }
    } catch (error) {
        console.error('Error fetching token:', error);
        tokenSection.innerHTML = `
            <h3>Your Order Token</h3>
            <p class="token-value">TOKEN-GENERATION-PENDING</p>
            <p class="token-status">Payment Status: <span class="${order.payment_status === 'paid' ? 'paid' : 'unpaid'}">${order.payment_status.toUpperCase()}</span></p>
            <p class="token-instructions">Your token is being generated. Please refresh in a moment.</p>
        `;
    }
}

// API base URL and auth headers
const API_BASE_URL = 'http://localhost:8000/api';

function getAuthHeaders() {
    const token = localStorage.getItem('smart_canteen_token');
    return token ? { 'Authorization': `Bearer ${token}` } : {};
}

// Process payment for an order
async function processPayment(orderId, amount) {
    const paymentStatusEl = document.getElementById('payment-status');
    const payNowBtn = document.getElementById('pay-now-btn');

    // Disable button and show loading state
    payNowBtn.disabled = true;
    payNowBtn.textContent = 'Processing...';
    paymentStatusEl.innerHTML = '<p class="loading">Processing payment...</p>';

    try {
        // Step 1: Create payment intent
        const intentResponse = await fetch(`${API_BASE_URL}/payment/create-intent/${orderId}`, {
            method: 'POST',
            headers: {
                ...getAuthHeaders(),
                'Content-Type': 'application/json',
            }
        });

        if (!intentResponse.ok) {
            throw new Error('Failed to create payment intent');
        }

        const intentData = await intentResponse.json();

        // In a real implementation, we would use a payment gateway SDK here
        // For simulation, we'll automatically confirm the payment after a short delay

        // Simulate payment processing delay
        await new Promise(resolve => setTimeout(resolve, 2000));

        // Step 2: Confirm payment
        const confirmResponse = await fetch(`${API_BASE_URL}/payment/confirm/${orderId}`, {
            method: 'POST',
            headers: {
                ...getAuthHeaders(),
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                payment_intent_id: intentData.payment_intent_id
            })
        });

        if (!confirmResponse.ok) {
            throw new Error('Failed to confirm payment');
        }

        const confirmData = await confirmResponse.json();

        // Update UI to show success
        paymentStatusEl.innerHTML = '<p class="success">Payment successful!</p>';
        payNowBtn.textContent = 'Paid';
        payNowBtn.disabled = true;

        // Refresh order details to show updated payment status
        setTimeout(() => {
            window.location.reload();
        }, 1500);

    } catch (error) {
        console.error('Payment error:', error);
        paymentStatusEl.innerHTML = `<p class="error">Payment failed: ${error.message}</p>`;
        payNowBtn.disabled = false;
        payNowBtn.textContent = 'Pay Now';
    }
}