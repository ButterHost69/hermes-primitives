const express = require('express');
const Product = require('../models/Product');

const router = express.Router();

// GET /api/products — list all products
router.get('/', async (req, res, next) => {
  try {
    const products = await Product.find().sort({ name: 1 });
    res.json(products);
  } catch (err) {
    next(err);
  }
});

// GET /api/products/:id — fetch a single product
router.get('/:id', async (req, res, next) => {
  try {
    const product = await Product.findById(req.params.id);
    if (!product) {
      return res.status(404).json({ message: 'Product not found' });
    }
    res.json(product);
  } catch (err) {
    next(err);
  }
});

module.exports = router;
