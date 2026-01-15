# Дизайн-система Xtrix

## Цветовая палитра

### Основные цвета

#### Primary (Синий - для акцентов и кнопок)
- `primary-50`: #eff6ff
- `primary-100`: #dbeafe
- `primary-200`: #bfdbfe
- `primary-300`: #93c5fd
- `primary-400`: #60a5fa
- `primary-500`: #3b82f6
- `primary-600`: #2563eb (Основной акцентный цвет)
- `primary-700`: #1d4ed8
- `primary-800`: #1e40af
- `primary-900`: #1e3a8a

**Использование:**
- Активные элементы навигации: `bg-primary-600`
- Основные кнопки: `bg-primary-600 hover:bg-primary-700`
- Ссылки и акценты: `text-primary-600`

### Нейтральные цвета (Gray scale)

#### Sidebar (Темная тема)
- Фон: `bg-gray-800` (#1f2937)
- Hover: `hover:bg-gray-700` (#374151)
- Текст: `text-gray-300` (#d1d5db)
- Активный элемент: `bg-primary-600` (#2563eb)

#### Таблицы
- Заголовок: `bg-gray-50` (#f9fafb)
- Границы: `divide-gray-200` (#e5e7eb)
- Hover строк: `hover:bg-gray-50` (#f3f4f6)

#### Фон
- Основной фон: `bg-gray-50` (#f9fafb)
- Карточки: `bg-white` (#ffffff)

## Типографика

### Размеры шрифтов
- `text-xs`: 0.75rem (12px) - Заголовки разделов, метки
- `text-sm`: 0.875rem (14px) - Мелкий текст, кнопки
- `text-base`: 1rem (16px) - Основной текст
- `text-lg`: 1.125rem (18px) - Подзаголовки
- `text-xl`: 1.25rem (20px) - Заголовки секций
- `text-2xl`: 1.5rem (24px) - Большие заголовки
- `text-3xl`: 1.875rem (30px) - Очень большие заголовки

### Веса шрифтов
- `font-normal`: 400 - Основной текст
- `font-medium`: 500 - Акценты, кнопки
- `font-semibold`: 600 - Заголовки
- `font-bold`: 700 - Важные заголовки

## Компоненты

### Кнопки

#### Primary Button
```jsx
<button className="px-6 py-2.5 bg-primary-600 text-white rounded-lg hover:bg-primary-700 disabled:opacity-50 disabled:cursor-not-allowed font-medium shadow-md hover:shadow-lg transition-all">
  Button Text
</button>
```

#### Secondary Button
```jsx
<button className="px-6 py-2.5 border border-gray-300 rounded-lg hover:bg-gray-50 font-medium text-gray-700 transition-all">
  Button Text
</button>
```

### Таблицы

#### Базовая таблица
```jsx
<table className="min-w-full divide-y divide-gray-200">
  <thead className="bg-gray-50">
    <tr>
      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
        Header
      </th>
    </tr>
  </thead>
  <tbody className="bg-white divide-y divide-gray-200">
    <tr className="hover:bg-gray-50">
      <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
        Content
      </td>
    </tr>
  </tbody>
</table>
```

### Навигация

#### Sidebar
- Фон: `bg-gray-800`
- Активный элемент: `bg-primary-600 text-white`
- Неактивный элемент: `text-gray-300 hover:bg-gray-700 hover:text-white`
- Заголовки разделов: `text-xs font-semibold text-gray-400 uppercase tracking-wider`

### Карточки

#### Базовая карточка
```jsx
<div className="bg-white rounded-lg shadow-md p-6">
  {/* Content */}
</div>
```

## Отступы и размеры

### Spacing
- `p-4`: 1rem (16px) - Стандартный внутренний отступ
- `p-6`: 1.5rem (24px) - Внутренний отступ карточек
- `px-4`: 1rem (16px) - Горизонтальный отступ
- `py-2`: 0.5rem (8px) - Вертикальный отступ
- `py-3`: 0.75rem (12px) - Вертикальный отступ таблиц
- `gap-6`: 1.5rem (24px) - Расстояние между элементами

### Border Radius
- `rounded`: 0.25rem (4px)
- `rounded-lg`: 0.5rem (8px) - Кнопки, карточки
- `rounded-xl`: 0.75rem (12px) - Большие карточки

### Shadows
- `shadow`: Базовая тень
- `shadow-md`: Средняя тень - карточки
- `shadow-lg`: Большая тень - hover состояния

## Примеры использования

### Layout (Сайдбар)
```jsx
<aside className="w-64 bg-gray-800 text-white">
  <nav>
    <Link className={isActive ? 'bg-primary-600 text-white' : 'text-gray-300 hover:bg-gray-700'}>
      Item
    </Link>
  </nav>
</aside>
```

### Формы
```jsx
<input className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-primary-500" />
```

### Кнопки в формах
```jsx
<div className="flex justify-end space-x-3 pt-4 border-t border-gray-200">
  <button className="px-6 py-2.5 border border-gray-300 rounded-lg hover:bg-gray-50 font-medium text-gray-700">
    Cancel
  </button>
  <button className="px-6 py-2.5 bg-primary-600 text-white rounded-lg hover:bg-primary-700 font-medium">
    Submit
  </button>
</div>
```

## Применение в проекте

Все цвета дизайн-системы определены в `tailwind.config.js` и могут быть использованы через классы Tailwind CSS:

- `bg-primary-600` - основной акцентный цвет
- `bg-gray-800` - фон сайдбара
- `bg-gray-50` - фон заголовков таблиц
- `text-primary-600` - цвет ссылок и акцентов

Все компоненты должны следовать этим правилам для обеспечения единообразия интерфейса.
