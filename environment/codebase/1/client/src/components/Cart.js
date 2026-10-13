function Cart({ items, onUpdateQuantity }) {
  if (items.length === 0) {
    return (
      <section className="cart">
        <h2>Cart</h2>
        <p>Your cart is empty.</p>
      </section>
    );
  }

  return (
    <section className="cart">
      <h2>Cart</h2>
      <ul>
        {items.map((item) => (
          <li key={item.productId}>
            <span>{item.name}</span>
            <span>
              ${item.price.toFixed(2)} ×{' '}
              <input
                type="number"
                min="1"
                value={item.quantity}
                onChange={(e) =>
                  onUpdateQuantity(item.productId, parseInt(e.target.value, 10) || 0)
                }
              />
            </span>
            <span>${(item.price * item.quantity).toFixed(2)}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

export default Cart;
