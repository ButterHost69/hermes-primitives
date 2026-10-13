import { useEffect, useState } from 'react';

function ProductList({ onAddToCart }) {
  const [products, setProducts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetch('/api/products')
      .then((res) => {
        if (!res.ok) throw new Error('Failed to load products');
        return res.json();
      })
      .then((data) => setProducts(data))
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <p>Loading products…</p>;
  if (error) return <p>Error: {error}</p>;

  return (
    <section className="product-list">
      <h2>Products</h2>
      <ul>
        {products.map((product) => (
          <li key={product._id}>
            <div>
              <strong>{product.name}</strong>
              <span>${product.price.toFixed(2)}</span>
              <span>In stock: {product.stock}</span>
            </div>
            <button
              onClick={() => onAddToCart(product)}
              disabled={product.stock <= 0}
            >
              Add to cart
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

export default ProductList;
