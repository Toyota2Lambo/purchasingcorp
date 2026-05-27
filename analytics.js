/**
 * Vercel Web Analytics initialization
 * Imports and initializes the @vercel/analytics package
 */
import { inject } from './analytics-lib.js';

// Initialize analytics
inject({
  mode: 'auto'
});
