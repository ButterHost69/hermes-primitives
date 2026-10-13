# Issue — Incorrect order totals when a discount is applied

## Summary

Checkout totals are wrong whenever a customer applies a percentage discount.
With a discount enabled, the computed order total is drastically off — it can
even become negative.

## Reproduce

```bash
cd server
node --test
```

The test `applies a percentage discount correctly` fails.

## Expected behaviour

Applying a `discountPercent` should reduce the subtotal by that *percentage*
(e.g. `10` means 10% off), then tax and shipping are added.

## Actual behaviour

The total comes out wrong (negative in the example), as if the discount were
applied as a raw value rather than a percentage.

## Where to look

The order-total calculation lives in `server/src/services/cart.js`.
