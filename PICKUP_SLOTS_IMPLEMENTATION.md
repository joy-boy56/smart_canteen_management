# Pickup Slot Management System Implementation

This document describes the pickup slot management system implemented for the Smart Canteen project, based on the requirements in the MASTER PROMPT section 6: "PREFERRED FOOD PICKUP TIME SLOTS".

## Overview

The implementation adds time-slot-aware capacity planning to the Smart Canteen system, allowing students to select specific pickup slots when placing orders and enabling administrators to manage slot configurations and capacity.

## Database Changes

Added a new `pickup_slots` table with the following fields:
- `id`: Primary key
- `start_time`: Start of the pickup slot (e.g., 12:00)
- `end_time`: End of the pickup slot (e.g., 12:15)
- `booking_cutoff`: When booking closes for this slot (optional)
- `max_orders`: Maximum orders allowed in this slot
- `current_orders`: Current confirmed orders count (updated atomically)
- `safety_factor`: Safety factor for capacity planning (0-1, default 0.8)
- `status`: Slot status (active/inactive/closed)
- `description`: Optional description of the slot
- `created_at`, `updated_at`: Timestamps

Relationship: One-to-Many with Order model (one slot can have many orders)

## API Endpoints

### Public Endpoints
- `GET /api/pickup-slots/active/` - Get all active slots for student selection

### Admin Endpoints (require admin authentication)
- `GET /api/pickup-slots/` - Get all slots
- `GET /api/pickup-slots/{slot_id}` - Get specific slot
- `POST /api/pickup-slots/` - Create new slot
- `PATCH /api/pickup-slots/{slot_id}` - Update slot
- `DELETE /api/pickup-slots/{slot_id}` - Delete slot
- `GET /api/pickup-slots/{slot_id}/stats` - Get slot statistics including utilization

## Frontend Changes

### Student Interface (cart.html/cart.js)
- Replaced datetime-local pickup time input with slot selection interface
- Fetches active slots from `/api/pickup-slots/active/` on page load
- Displays slots as radio buttons with:
  - Time range (start - end)
  - Capacity information (remaining / effective capacity)
  - Slot description (if provided)
  - Visual indication of full slots (disabled and styled differently)
- Handles slot selection and sends `pickup_slot_id` with order creation
- Prevents selection of full slots

### Order Confirmation (order.js)
- Displays assigned pickup slot time range instead of requested pickup time
- Shows slot description if available
- Falls back to showing requested pickup time if no slot was assigned

### Admin Interface (admin/slots.html and admin/slots.js)
- Complete CRUD interface for managing pickup slots
- Form for creating new slots with validation:
  - Start/end time validation (end must be after start)
  - Booking cutoff validation (must be before start time)
  - Maximum orders validation (> 0)
  - Safety factor validation (between 0 and 1)
- Slot listing shows:
  - Slot time range
  - Booking cutoff
  - Current vs maximum orders
  - Effective capacity (max_orders × safety_factor)
  - Remaining capacity
  - Utilization percentage
  - Status indicators
- Edit and delete functionality (delete fully implemented, edit placeholder)

## Integration with Order Placement

When placing an order:
1. If a `pickup_slot_id` is provided:
   - Validates the slot exists and is active
   - Checks if booking cutoff has passed
   - Calculates effective capacity (max_orders × safety_factor)
   - Verifies current orders < effective capacity
   - Atomically increments the slot's current_orders count
   - Associates the order with the slot
2. If no slot is selected, the order proceeds without slot assignment (backward compatibility)

## Capacity Management

The system implements capacity checking as follows:
- Effective capacity = floor(max_orders × safety_factor)
- An order can only be placed if current_orders < effective capacity
- When an order is placed, current_orders is incremented atomically
- This prevents overselling even with concurrent requests

## Limitations & Future Improvements

1. **Waiting Time Estimates**: The current implementation does not show expected preparation or waiting time estimates in the slot selection interface. This would require integrating with the queueing theory analytics system.

2. **Temporary Reservations**: The MASTER PROMPT mentions setting "a temporary reservation timeout for unpaid online-payment attempts." This feature is not implemented - currently, slot capacity is reserved immediately upon order placement regardless of payment status.

3. **Capacity Consumption Rules**: Per the MASTER PROMPT, capacity consumption rules are more nuanced:
   - Confirmed PAID orders consume capacity
   - Accepted UNPAID pay-at-canteen orders consume capacity  
   - Pending online-payment orders may temporarily reserve capacity
   The current implementation counts all orders toward capacity upon placement.

4. **Slot Analytics**: While basic statistics are available, advanced analytics showing slot utilization over time, historical booking patterns, etc., are not implemented.

5. **Admin Slot Editing**: The edit functionality in the admin interface is currently a placeholder and would need to be fully implemented for production use.

## Files Modified/Added

### Backend
- `backend/database/models.py` - Added PickupSlot model and updated Order model
- `backend/schemas/pickup_slots.py` - New Pydantic schemas
- `backend/routers/pickup_slots.py` - New API router
- `backend/main.py` - Added pickup_slots router import and inclusion
- `backend/schemas/orders.py` - Updated OrderCreate and OrderResponse schemas
- `backend/routers/orders.py` - Updated order placement logic

### Frontend
- `frontend/student/cart.html` - Replaced pickup time input with slot selection
- `frontend/student/cart.js` - Added slot fetching, selection, and order integration
- `frontend/student/order.js` - Updated to display pickup slot information
- `frontend/admin/slots.html` - New admin slot management page
- `frontend/admin/slots.js` - New admin slot management logic
- `frontend/admin/dashboard.html` - Added link to slots management
- `frontend/admin/reports.html` - Placeholder reports page (to fix broken link)
- `frontend/css/style.css` - Added CSS styles for slot selection UI

### Documentation
- `PICKUP_SLOTS_IMPLEMENTATION.md` - This document