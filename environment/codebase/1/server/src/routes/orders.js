const express = require('express');
const Order = require('../models/Order');
const { computeOrderTotal } = require('../services/cart');

const router = express.Router();

// POST /api/orders — compute the total and persist a new order
router.post('/', async (req, res, next) => {
  try {
    const { items, discountPercent = 0, taxRate = 0, shipping = 0 } = req.body;

    if (!Array.isArray(items) || items.length === 0) {
      return res.status(400).json({ message: 'Order must include at least one item' });
    }

    const total = computeOrderTotal(items, { discountPercent, taxRate, shipping });

    const order = await Order.create({
      items,
      discountPercent,
      taxRate,
      shipping,
      total,
    });

    res.status(201).json(order);
  } catch (err) {
    next(err);
  }
});

module.exports = router;
