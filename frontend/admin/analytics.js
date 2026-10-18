// Analytics page functionality
document.addEventListener('DOMContentLoaded', () => {
    // Check if user is admin
    const token = localStorage.getItem('smart_canteen_token');
    if (!token) {
        window.location.href = '../login.html';
        return;
    }

    // Load analytics on initial load
    loadAnalytics();

    // Setup event listeners
    document.getElementById('refresh-btn').addEventListener('click', loadAnalytics);

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

// Load analytics data
async function loadAnalytics() {
    try {
        // Show loading states
        document.getElementById('current-status').innerHTML = '<div class="loading">Loading system status...</div>';
        document.getElementById('performance-metrics').innerHTML = '<div class="loading">Loading performance metrics...</div>';
        document.getElementById('recommendations').innerHTML = '<div class="loading">Generating recommendations...</div>';

        // Fetch analytics data from the API
        const analyticsResponse = await fetch(`${API_BASE_URL}/admin/analytics/analyze`, {
            headers: {
                ...getAuthHeaders(),
                'Content-Type': 'application/json',
            }
        });

        if (!analyticsResponse.ok) {
            throw new Error('Failed to load analytics data');
        }

        const analyticsData = await analyticsResponse.json();

        // Update UI with analytics data
        updateCurrentStatus(analyticsData);
        updatePerformanceMetrics(analyticsData);
        updateRecommendations(analyticsData);

    } catch (error) {
        console.error('Error loading analytics:', error);
        document.getElementById('current-status').innerHTML = `<p class="error">Failed to load system status. Please try again later.</p>`;
        document.getElementById('performance-metrics').innerHTML = `<p class="error">Failed to load performance metrics. Please try again later.</p>`;
        document.getElementById('recommendations').innerHTML = `<p class="error">Failed to generate recommendations. Please try again later.</p>`;
    }
}

// Update current status section
function updateCurrentStatus(data) {
    const container = document.getElementById('current-status');
    if (!container) return;

    const { observation_period, data_source, rate_estimate } = data;
    const { lambda_rate, mu_rate, utilization, active_counters, stable } = rate_estimate;

    // Format the observation period
    const formattedPeriod = observation_period || 'Unknown';

    container.innerHTML = `
        <p><strong>Observation Period:</strong> ${formattedPeriod}</p>
        <p><strong>Data Source:</strong> ${data_source}</p>
        <p><strong>Arrival Rate (λ):</strong> ${lambda_rate.toFixed(2)} customers/minute</p>
        <p><strong>Service Rate (μ):</strong> ${mu_rate.toFixed(2)} customers/minute/counter</p>
        <p><strong>Active Counters:</strong> ${active_counters}</p>
        <p><strong>System Utilization (ρ):</strong> ${(utilization * 100).toFixed(1)}% ${stable ? '' : '(UNSTABLE)'}</p>
        <p><strong>System Status:</strong> <span class="${stable ? 'stable' : 'unstable'}">${stable ? 'Stable' : 'Unstable (Utilization ≥ 100%)'}</span></p>
    `;
}

// Update performance metrics section
function updatePerformanceMetrics(data) {
    const container = document.getElementById('performance-metrics');
    if (!container) return;

    const { rate_estimate } = data;
    const { probability_wait, expected_queue_length, expected_waiting_time, expected_total_time } = rate_estimate;

    container.innerHTML = `
        <p><strong>Probability of Waiting:</strong> ${(probability_wait * 100).toFixed(1)}%</p>
        <p><strong>Expected Queue Length:</strong> ${expected_queue_length.toFixed(2)} customers</p>
        <p><strong>Expected Waiting Time:</strong> ${expected_waiting_time.toFixed(2)} minutes</p>
        <p><strong>Expected Total Time in System:</strong> ${expected_total_time.toFixed(2)} minutes</p>
    `;
}

// Update recommendations section
function updateRecommendations(data) {
    const container = document.getElementById('recommendations');
    if (!container) return;

    const { rate_estimate } = data;
    const { utilization, active_counters, lambda_rate, mu_rate, stable } = rate_estimate;

    let recommendations = [];

    // Generate recommendations based on the analytics
    if (!stable) {
        recommendations.push({
            priority: 'high',
            text: 'System is unstable (utilization ≥ 100%). Immediately increase service capacity or reduce arrival rate.'
        });
    } else if (utilization > 0.9) {
        recommendations.push({
            priority: 'high',
            text: 'High utilization (>90%). Consider adding more service counters or improving service efficiency.'
        });
    } else if (utilization > 0.7) {
        recommendations.push({
            priority: 'medium',
            text: 'Moderate utilization (70-90%). Monitor system performance and consider optimization during peak hours.'
        });
    } else {
        recommendations.push({
            priority: 'low',
            text: 'Low utilization (<70%). System has capacity to handle increased demand.'
        });
    }

    // Specific recommendations based on queue length and waiting time
    const { expected_queue_length, expected_waiting_time } = rate_estimate;
    if (expected_waiting_time > 5) { // More than 5 minutes waiting
        recommendations.push({
            priority: 'high',
            text: 'Expected waiting time is high (>5 minutes). Consider adding more counters or optimizing service process.'
        });
    } else if (expected_waiting_time > 3) { // More than 3 minutes waiting
        recommendations.push({
            priority: 'medium',
            text: 'Expected waiting time is moderate (3-5 minutes). Look for opportunities to reduce wait times.'
        });
    }

    // If no specific recommendations, add a general one
    if (recommendations.length === 0) {
        recommendations.push({
            priority: 'info',
            text: 'System is operating within normal parameters. Continue regular monitoring.'
        });
    }

    // Format recommendations for display
    const recommendationsHTML = recommendations.map(rec => {
        const priorityClass = `priority-${rec.priority}`;
        return `
            <div class="recommendation-item ${priorityClass}">
                <strong>[${rec.priority.toUpperCase()}]</strong> ${rec.text}
            </div>
        `;
    }).join('');

    container.innerHTML = `
        <h4>Recommendations:</h4>
        <div class="recommendations-list">
            ${recommendationsHTML}
        </div>
    `;
}