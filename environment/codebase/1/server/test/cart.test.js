const test = require('node:test');
const assert = require('node:assert');
const { computeOrderTotal } = require('../src/services/cart');

test('applies a percentage discount correctly', () => {
  const items = [
    { price: 10, quantity: 2 },
    { price: 20, quantity: 1 },
  ];
  const total = computeOrderTotal(items, { discountPercent: 10, taxRate: 0.08, shipping: 5 });
  assert.strictEqual(total, 43.88);
});

test('no discount and no tax returns the subtotal', () => {
  const items = [{ price: 5, quantity: 3 }];
  assert.strictEqual(computeOrderTotal(items), 15);
});
