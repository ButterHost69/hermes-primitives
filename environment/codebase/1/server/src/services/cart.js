const computeOrderTotal = (items, { discountPercent = 0, taxRate = 0, shipping = 0 } = {}) => {
  const subtotal = items.reduce((sum, it) => sum + it.price * it.quantity, 0);
  const discount = subtotal * discountPercent;
  const taxable = subtotal - discount;
  const tax = taxable * taxRate;
  return Math.round((taxable + tax + shipping) * 100) / 100;
};

module.exports = { computeOrderTotal };
