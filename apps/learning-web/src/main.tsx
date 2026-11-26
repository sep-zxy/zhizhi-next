import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './styles/fonts.css';
import './styles/zhizhi-tokens.css';
import './growth/GrowthBase.css';
import './i18n/bootstrap';
import App from './App';
import { MantineProvider } from '@mantine/core';
import '@mantine/core/styles.css';
import { growthMantineTheme } from './styles/zhizhi-mantine-theme';

const container = document.getElementById('root');
if (!container) {
  throw new Error('Root element #root missing from index.html');
}

createRoot(container).render(
  <StrictMode>
    <MantineProvider theme={growthMantineTheme} forceColorScheme="light"><App /></MantineProvider>
  </StrictMode>,
);
