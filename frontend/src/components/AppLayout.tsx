import { Layout, Menu } from 'antd'
import { FileOutlined, HistoryOutlined } from '@ant-design/icons'
import { useNavigate, useLocation, Outlet } from 'react-router-dom'

const { Sider, Content, Header } = Layout

const menuItems = [
  { key: '/', icon: <FileOutlined />, label: '脚本库' },
  { key: '/history', icon: <HistoryOutlined />, label: '运行历史' },
]

export default function AppLayout() {
  const navigate = useNavigate()
  const location = useLocation()

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider collapsible theme="dark">
        <div style={{
          height: 48,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: '#fff',
          fontSize: 18,
          fontWeight: 'bold',
        }}>
          ScriptHub
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[location.pathname]}
          items={menuItems}
          onClick={({ key }) => navigate(key)}
        />
      </Sider>
      <Layout>
        <Header style={{ background: '#fff', padding: '0 24px', fontSize: 16 }}>
          {location.pathname === '/' ? '脚本库' : location.pathname === '/history' ? '运行历史' : ''}
        </Header>
        <Content style={{ margin: 16, padding: 24, background: '#fff', borderRadius: 8 }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  )
}
