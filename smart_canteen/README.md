# Smart Canteen — Queue Optimization and Intelligent Capacity Planning

An Operations Research and full-stack canteen management prototype combining **food ordering**, **preferred pickup time slots**, **cryptographically opaque PAID/UNPAID QR tokens**, **Direct UPI & Razorpay payment workflows**, **M/M/c (Erlang-C) queueing analytics**, **time-slot-aware integer counter optimization**, and **reproducible discrete-event stochastic simulation (DES)**.

---

## 1. Architecture & Directory Structure

```text
smart_canteen/
├── backend/
│   ├── __init__.py
│   ├── config.py                  # Environment configuration & payment mode checks
│   ├── database.py                # SQLite schema, foreign keys, indexes & atomic BEGIN IMMEDIATE sessions
│   ├── models.py                  # Domain enums, state transition matrix & integer paise helpers
│   ├── schemas.py                 # Pydantic request/response schemas & validators
│   ├── security.py                # Scrypt password hashing, HMAC-SHA256 sessions & RBAC dependencies
│   ├── main.py                    # FastAPI REST endpoints & static frontend serving
│   ├── or_engine/
│   │   ├── __init__.py
│   │   ├── queueing.py            # Rate estimation, M/M/c Erlang-C, slot capacity K_s & DP integer optimizer
│   │   └── simulation.py          # Fixed-seed event-driven FIFO DES engine & 95% confidence intervals
│   └── services/
│       ├── __init__.py
│       ├── order_service.py       # Atomic slot reservation, expired hold cleanup & single token redemption
│       └── payment_service.py     # Direct UPI URI builder, UTR aid, manual reconciliation & Razorpay adapter
├── frontend/
│   ├── index.html                 # Student & Admin responsive web application (HTML5/CSS3/ES JS + Chart.js)
│   └── admin.html                 # Direct Admin entry point
├── scripts/
│   ├── init_db.py                 # Initializes SQLite schema & default settings
│   ├── create_admin.py            # Secure CLI tool to create or promote an administrator account
│   └── seed_demo.py               # Seeds menu items, pickup slots & labelled SYNTHETIC_DEMO observations
├── tests/
│   └── test_all.py                # Automated test suite (Math/OR, DES, Concurrency, Payments, Tokens, RBAC)
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

---

## 2. Installation & Running on Windows PowerShell

Open **Windows PowerShell** in the project root directory and run:

```powershell
# 1. Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\Activate.ps1

# 2. Install dependencies
python -m pip install -r requirements.txt

# 3. Copy environment template
Copy-Item .env.example .env

# 4. Initialize the SQLite database schema
python scripts/init_db.py

# 5. Securely create your first Administrator account (no hardcoded default admin password)
python scripts/create_admin.py --name "Prof. R. Krishnan" --email "admin@smartcanteen.edu.in" --phone "9447000001"

# 6. (Optional) Seed demo menu items, pickup slots, and labelled SYNTHETIC_DEMO observations
python scripts/seed_demo.py

# 7. Start the FastAPI server
python -m uvicorn backend.main:app --reload
```

### Local Application URLs

- **Student Ordering Interface**: `http://127.0.0.1:8000/` (or `http://127.0.0.1:8000/student`)
- **Admin & Operations Research Studio**: `http://127.0.0.1:8000/admin`
- **API Health Check**: `http://127.0.0.1:8000/api/health`
- **Interactive OpenAPI / Swagger Docs**: `http://127.0.0.1:8000/docs`

---

## 3. Payment Architecture: Direct UPI vs. Payment Gateway

1. **Direct UPI Mode (`DIRECT_UPI` — Default for local prototype)**:
   - Administrators configure the receiving UPI ID/VPA (e.g., `canteen@sbi`) under **Admin Settings → Payments**.
   - At checkout, the backend generates a standard RFC-3986 URI-encoded `upi://pay` intent string and renders a **Payment Initiation QR** for the exact server-calculated order total.
   - Students may optionally report their 12-digit UTR/reference number as a reconciliation aid.
   - **Strict Payment Integrity**: Initiating UPI payment or submitting a UTR **never** marks an order `PAID`. The order remains `PENDING` until an authenticated administrator verifies receipt against the actual bank/UPI statement in **Admin → Payment Reconciliation**.
2. **Payment Gateway Mode (`GATEWAY_UPI` — Razorpay Integration)**:
   - Set `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, and `RAZORPAY_WEBHOOK_SECRET` in `.env`.
   - Payment confirmation requires valid HMAC-SHA256 signature verification via `/api/payments/gateway/callback` or raw body webhook signature verification via `/api/payments/gateway/webhook` (with idempotent `webhook_event_id` and `provider_payment_id` deduplication).
   - **Important Distinction**: In Gateway mode, funds settle to the merchant bank account linked to the Razorpay account credentials—merely typing a UPI ID in Admin Settings does not redirect gateway settlement.
3. **Pay at Canteen (`PAY_AT_CANTEEN`)**:
   - Reserves slot capacity atomically and immediately issues an amber **`UNPAID`** collection token showing the exact balance due (`Payment due at canteen: ₹XXX.XX`).
   - When the student pays by Cash or Counter UPI upon collection, the administrator records the payment, upgrading the order and token badge to green **`PAID`** without altering the order's identity or breaking its QR code.

---

## 4. Operations Research, Queueing Theory & Slot-Aware Optimization

- **Empirical Rate Estimation**:
  $$\lambda = rac{	ext{observed arrivals}}{	ext{observation duration}}, \qquad \mu = rac{1}{	ext{mean service time}}$$
- **M/M/c Analytical Queueing Model (Erlang-C)**:
  $$ho = rac{\lambda}{c\mu}, \qquad a = rac{\lambda}{\mu}, \qquad P_0 = \left[\sum_{n=0}^{c-1}rac{a^n}{n!} + rac{a^c}{c!(1-ho)}ight]^{-1}$$
  $$P_w = rac{a^c}{c!(1-ho)}P_0, \qquad L_q = rac{P_0 a^c ho}{c!(1-ho)^2}, \qquad W_q = rac{L_q}{\lambda}, \qquad L = L_q + rac{\lambda}{\mu}, \qquad W = W_q + rac{1}{\mu}$$
  Whenever $ho \ge 1$, the engine flags the slot as `UNSTABLE_OVERLOADED` and refuses to report finite steady-state waiting predictions.
- **Time-Slot-Aware Capacity & Integer Resource Optimization**:
  $$K_s = \lfloor lpha_s c_s \mu_s \Delta_s floor, \qquad K_s^{	ext{effective}} = \min(K_s, K_s^{	ext{admin cap}}), \qquad R_s = \max(0, K_s^{	ext{effective}} - B_s)$$
  The optimizer uses exact Dynamic Programming over integer counter allocations $c_s \in [c_{\min}, c_{\max}]$ subject to $\sum_s c_s \le C_{	ext{available}}$ to minimize total expected waiting cost plus staffing cost while preserving all existing confirmed student bookings.

---

## 5. Running Automated Tests

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
# or if pytest is installed:
python -m pytest -v
```
