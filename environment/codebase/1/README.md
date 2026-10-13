# ShopFront

ShopFront is a minimal but realistic e-commerce store built on the **MERN** stack.
It lists products, lets shoppers add items to a cart, and places orders whose
totals (discounts, tax, and shipping) are computed on the server.

## Stack

| Layer      | Technology                          | Where                       |
| ---------- | ----------------------------------- | --------------------------- |
| Database   | MongoDB (via Mongoose)              | `server/src/models/`        |
| Backend    | Express + Node.js                   | `server/src/`               |
| Frontend   | React 18                            | `client/src/`               |

## Project layout

```
server/
  package.json
  .gitignore
  src/
    app.js              Express app: JSON parser + /api/products, /api/orders
    config.js           PORT and MONGODB_URI from env with sane defaults
    db.js               Mongoose connection helper
    models/Product.js   Product schema (name, price, stock)
    models/Order.js     Order schema (items, discount, tax, shipping, total)
    routes/products.js  GET / (list) and GET /:id (one product)
    routes/orders.js    POST / (compute total, save Order)
    services/cart.js    Pure order-total helper (no Express/Mongoose imports)
  test/
    cart.test.js        node:test suite for the cart service
client/
  package.json
  public/index.html
  src/
    index.js
    App.js
    components/ProductList.js
    components/Cart.js
    components/Checkout.js
README.md
```

## Prerequisites

- Node.js (18+)
- A running MongoDB instance (local or Atlas)

## Install

```bash
cd server && npm install
cd ../client && npm install
```

## Configure

The server reads configuration from environment variables (optionally a
`server/.env` file):

- `PORT` — HTTP port (default `5000`)
- `MONGODB_URI` — connection string (default `mongodb://127.0.0.1:27017/shopfront`)

Example `server/.env`:

```bash
PORT=5000
MONGODB_URI=mongodb://127.0.0.1:27017/shopfront
```

## Run

Start the API (and connect to MongoDB):

```bash
cd server && npm start
```

Start the React dev server (proxies `/api` to the backend on port 5000):

```bash
cd client && npm start
```

Open http://localhost:3000 to browse the store.

## Tests

The cart service is a pure function, so its tests run with no dependencies
installed. Run them with either:

```bash
cd server && npm test
```

or

```bash
cd server && node --test
```
