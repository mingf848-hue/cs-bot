export interface UserLoginType {
  username: string
  password: string
}

export interface UserType {
  username: string
  password?: string
  realName?: string
  role: string
  roleId: string
  permissions?: string[]
}
