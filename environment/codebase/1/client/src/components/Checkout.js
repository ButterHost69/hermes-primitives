import { useState } from 'react';

function Checkout({ items }) {
  const [status, setStatus] = useState('idle'); // idle | submitting | done | error
  const [orderTotal, setOrderTotal] = useState(null);
  const [error, setError] = useState(null);

  const subtotal = items.reduce((sum, item) => sum + item.price * item.quantity, 0);

  const handleCheckout = async () => {
    setStatus('submitting');
    setError(null);
    try {
      const res = await fetch('/api/orders', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          items,
          discountPercent: 0,
          taxRate: 0.08,
          shipping: 5,
        }),
      });
      if (!res.ok) throw new Error('Checkout failed');
      const order = await res.json();
      setOrderTotal(order.total);
      setStatus('done');
    } catch (err) {
      setError(err.message);
      setStatus('error');
    }
  };

  return (
    <section className="checkout">
      <h2>Checkout</h2>
      <p>Subtotal: ${subtotal.toFixed(2)}</p>
      {orderTotal !== null && <p>Total: ${orderTotal.toFixed(2)}</p>}
      <button
        onClick={handleCheckout}
        disabled={items.length === 0 || status === 'submitting'}
      >
        {status === 'submitting' ? 'Placing order…' : 'Place order'}
      </button>
      {status === 'done' && <p>Order placed successfully!</p>}
      {status === 'error' && <p>Error: {error}</p>}
    </section>
  );
}

export default Checkout;
