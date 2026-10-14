'use strict';

// Oracle test for environment/codebase/1 (ShopFront).
//
// The bug: `computeOrderTotal` applies `discountPercent` as a raw multiplier
// instead of dividing by 100, so a 10% discount removes 10x the subtotal.
//
// These tests import the real cart service from the codebase (not a copy), so
// they fail while the bug is present and pass once the percentage fix lands.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

// Locate codebase/1 regardless of whether the tests run from solution/1, the
// repository root, or a copy placed inside the codebase itself.
function resolveCodebase(marker, number) {
  const candidates = [];
  let dir = __dirname;
  for (let i = 0; i < 6; i += 1) {
    candidates.push(path.join(dir, 'codebase', String(number)));
    candidates.push(path.join(dir, String(number)));
    candidates.push(dir);
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  let cwd = process.cwd();
  for (let i = 0; i < 6; i += 1) {
    candidates.push(path.join(cwd, 'codebase', String(number)));
    candidates.push(cwd);
    const parent = path.dirname(cwd);
    if (parent === cwd) break;
    cwd = parent;
  }
  for (const candidate of candidates) {
    if (fs.existsSync(path.join(candidate, marker))) return candidate;
  }
  throw new Error(`Unable to locate codebase ${number} (looked for ${marker})`);
}

const codebase = resolveCodebase(path.join('server', 'src', 'services', 'cart.js'), 1);
const { computeOrderTotal } = require(path.join(
  codebase,
  'server',
  'src',
  'services',
  'cart.js'
));

test('applies a percentage discount (10% off) before tax and shipping', () => {
  const items = [
    { price: 10, quantity: 2 },
    { price: 20, quantity: 1 },
  ];
  const total = computeOrderTotal(items, {
    discountPercent: 10,
    taxRate: 0.08,
    shipping: 5,
  });
  assert.strictEqual(total, 43.88);
});

test('a 10% discount removes exactly 10% of the subtotal', () => {
  const items = [{ price: 200, quantity: 1 }];
  assert.strictEqual(computeOrderTotal(items, { discountPercent: 10 }), 180);
});

test('zero discount leaves the subtotal, tax, and shipping untouched', () => {
  const items = [{ price: 100, quantity: 1 }];
  assert.strictEqual(
    computeOrderTotal(items, { discountPercent: 0, taxRate: 0.08, shipping: 5 }),
    113
  );
});

test('a 100% discount zeroes the taxable amount but keeps shipping', () => {
  const items = [{ price: 40, quantity: 1 }];
  assert.strictEqual(
    computeOrderTotal(items, { discountPercent: 100, shipping: 7 }),
    7
  );
});

test('applies a 25% discount with neither tax nor shipping', () => {
  const items = [{ price: 80, quantity: 1 }];
  assert.strictEqual(computeOrderTotal(items, { discountPercent: 25 }), 60);
});

test('applies the discount before computing tax (50% off, 10% tax)', () => {
  const items = [{ price: 100, quantity: 1 }];
  assert.strictEqual(
    computeOrderTotal(items, { discountPercent: 50, taxRate: 0.1 }),
    55
  );
});

test('rounds the final total to two decimals', () => {
  const items = [{ price: 19.99, quantity: 1 }];
  assert.strictEqual(computeOrderTotal(items, { discountPercent: 15 }), 16.99);
});

test('a valid percent never produces a negative total', () => {
  const items = [{ price: 40, quantity: 1 }];
  assert.strictEqual(computeOrderTotal(items, { discountPercent: 10 }), 36);
  assert.ok(computeOrderTotal(items, { discountPercent: 100 }) >= 0);
});

test('the total decreases as the discount percent increases', () => {
  const items = [{ price: 40, quantity: 1 }];
  const none = computeOrderTotal(items, { discountPercent: 0 });
  const ten = computeOrderTotal(items, { discountPercent: 10 });
  const twentyFive = computeOrderTotal(items, { discountPercent: 25 });
  assert.ok(none > ten, `${none} should exceed ${ten}`);
  assert.ok(ten > twentyFive, `${ten} should exceed ${twentyFive}`);
});

test('defaults to no discount, no tax, and no shipping', () => {
  const items = [{ price: 5, quantity: 3 }];
  assert.strictEqual(computeOrderTotal(items), 15);
});

test('handles an empty cart', () => {
  assert.strictEqual(computeOrderTotal([]), 0);
  assert.strictEqual(computeOrderTotal([], { shipping: 5 }), 5);
});
