const express = require('express');
const cors = require('cors');
const config = require('./config');
const connectDB = require('./db');
const productsRouter = require('./routes/products');
const ordersRouter = require('./routes/orders');

const app = express();

app.use(cors());
app.use(express.json());

app.use('/api/products', productsRouter);
app.use('/api/orders', ordersRouter);

// 404 handler
app.use((req, res) => {
  res.status(404).json({ message: 'Not found' });
});

// error handler
app.use((err, req, res, next) => {
  console.error(err);
  res.status(500).json({ message: 'Internal server error' });
});

// Start the server when run directly (e.g. `npm start`).
if (require.main === module) {
  connectDB().then(() => {
    app.listen(config.port, () => {
      console.log(`ShopFront server listening on port ${config.port}`);
    });
  });
}

module.exports = app;
