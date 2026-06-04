# Menu Schema

## Overview

Hệ thống menu gồm 2 phần chính:

```text
Root
├─ food_conditions
└─ groups
```

* `food_conditions`: danh sách dữ liệu dùng chung toàn hệ thống.
* `groups`: danh sách nhóm món trong menu.

---

# Root Schema

```json
{
  "food_conditions": [],
  "groups": []
}
```

| Field           | Type            | Description                  |
| --------------- | --------------- | ---------------------------- |
| food_conditions | FoodCondition[] | Danh sách dữ liệu dùng chung |
| groups          | Group[]         | Danh sách nhóm món           |

---

# FoodCondition

Là dữ liệu gốc được tái sử dụng trong nhiều menu khác nhau.

Ví dụ:

* Tofu
* Lachs
* Cola
* Fanta
* Cheese
* Bacon
* Đường
* Đá

## Schema

```json
{
  "plu": "12312",
  "name": "Đường",
  "base_price": 0.3,
  "status": 1
}
```

## Fields

| Field      | Type   | Description  |
| ---------- | ------ | ------------ |
| plu        | string | Mã sản phẩm  |
| name       | string | Tên          |
| base_price | number | Giá mặc định |
| status     | number | Trạng thái   |

---

# Group

```json
{
  "name": "Burger",
  "foods": []
}
```

| Field | Type   |
| ----- | ------ |
| name  | string |
| foods | Food[] |

---

# Food

Mỗi món ăn hoặc đồ uống trong menu.

## Schema

```json
{
  "name": "Hamburger",
  "plu": "B001",
  "type": 2,
  "kind": 1,
  "price_in": 3.7,
  "price_out": 3.7,
  "options": []
}
```

## Fields

| Field     | Type          | Description              |
| --------- | ------------- | ------------------------ |
| name      | string        | Tên món                  |
| plu       | string | null | Mã sản phẩm              |
| type      | number        | `1=drink`, `2=food`      |
| kind      | number        | `1=common`, `5=combo`    |
| price_in  | number        | Giá tại quán             |
| price_out | number        | Giá mang đi              |
| options   | OptionGroup[] | Danh sách option         |
| foods     | Food[]        | Chỉ tồn tại khi `kind=5` |

---

# Food Type

| Value | Meaning |
| ----- | ------- |
| 1     | Drink   |
| 2     | Food    |

---

# Food Kind

| Value | Meaning |
| ----- | ------- |
| 1     | Common  |
| 5     | Combo   |

---

# OptionGroup

Ví dụ:

* Size
* Ingredient
* Topping
* Sauce
* Drink Choice

## Schema

```json
{
  "name": "Size",
  "option": 1,
  "type": 0,
  "food_datas": []
}
```

## Fields

| Field      | Type       | Description                         |
| ---------- | ---------- | ----------------------------------- |
| name       | string     | Tên option                          |
| option     | number     | `0=optional`, `1=required`          |
| type       | number     | `0=single choose`, `1=multi choose` |
| food_datas | FoodData[] | Danh sách lựa chọn                  |

---

# FoodData

Là dữ liệu được sử dụng thực tế trong menu.

FoodData tham chiếu tới một FoodCondition thông qua `plu`.

Giá trong menu được lấy từ `price`, không phải `base_price`.

## Schema

```json
{
  "name_food": "Đường",
  "plu": "12312",
  "price": 0.5,
  "required": true,
  "status": 1
}
```

## Fields

| Field     | Type    | Description                  |
| --------- | ------- | ---------------------------- |
| name_food | string  | Tên hiển thị trong menu      |
| plu       | string  | Tham chiếu tới FoodCondition |
| price     | number  | Giá thực tế trong menu       |
| required  | boolean | Bắt buộc hay không           |
| status    | number  | Trạng thái                   |

---

# Combo

Combo là một Food có:

```json
{
  "kind": 5
}
```

## Rules

* `kind=5` là combo.
* Combo chứa `foods`.
* Các phần tử trong `foods` là Food hoàn chỉnh.
* Tất cả food bên trong combo luôn có `kind=1`.
* Food trong combo vẫn có:

  * options
  * plu
  * price_in
  * price_out
  * type

---

# Example

```json
{
  "food_conditions": [
    {
      "plu": "ING001",
      "name": "Tofu",
      "base_price": 4.9,
      "status": 1
    },
    {
      "plu": "TOP001",
      "name": "Đường",
      "base_price": 0.3,
      "status": 1
    }
  ],
  "groups": [
    {
      "name": "Soup",
      "foods": [
        {
          "name": "Miso Suppe",
          "plu": "SOUP001",
          "type": 2,
          "kind": 1,
          "price_in": 0,
          "price_out": 0,
          "options": [
            {
              "name": "Ingredient",
              "option": 1,
              "type": 0,
              "food_datas": [
                {
                  "name_food": "Tofu",
                  "plu": "ING001",
                  "price": 4.9,
                  "required": true,
                  "status": 1
                }
              ]
            }
          ]
        }
      ]
    }
  ]
}
```
