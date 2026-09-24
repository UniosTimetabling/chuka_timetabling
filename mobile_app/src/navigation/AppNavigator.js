import React from 'react';
import { NavigationContainer } from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import { TouchableOpacity, Text } from 'react-native';
import { useAuth } from '../context/AuthContext';
import { COLORS } from '../theme/colors';
import AppLoadingScreen from '../components/AppLoadingScreen';

import WelcomeScreen from '../screens/WelcomeScreen';
import OnboardingScreen from '../screens/OnboardingScreen';
import RoleSelectScreen from '../screens/RoleSelectScreen';
import LoginScreen from '../screens/LoginScreen';
import TimetableScreen from '../screens/TimetableScreen';
import EventsScreen from '../screens/EventsScreen';
import EventDetailScreen from '../screens/EventDetailScreen';
import QRShareScreen from '../screens/QRShareScreen';
import QRScanScreen from '../screens/QRScanScreen';
import SavedTimetablesScreen from '../screens/SavedTimetablesScreen';
import SettingsScreen from '../screens/SettingsScreen';
import AddCourseScreen from '../screens/AddCourseScreen';
import FeedbackScreen from '../screens/FeedbackScreen';

const Stack = createNativeStackNavigator();

export default function AppNavigator() {
  const { user, loading, onboardingSeen } = useAuth();

  if (loading) {
    return <AppLoadingScreen label="Getting things ready…" />;
  }

  return (
    <NavigationContainer>
      <Stack.Navigator
        initialRouteName={!user ? (onboardingSeen ? 'Welcome' : 'Onboarding') : undefined}
        screenOptions={{
          headerStyle: { backgroundColor: COLORS.white },
          headerTintColor: COLORS.black,
          headerShadowVisible: false,
          headerTitleStyle: { fontWeight: '700' },
        }}
      >
        {!user ? (
          <>
            <Stack.Screen name="Onboarding" component={OnboardingScreen} options={{ headerShown: false }} />
            <Stack.Screen name="Welcome" component={WelcomeScreen} options={{ headerShown: false }} />
            <Stack.Screen name="RoleSelect" component={RoleSelectScreen} options={{ headerShown: false }} />
            <Stack.Screen name="Login" component={LoginScreen} options={{ headerShown: false }} />
          </>
        ) : (
          <>
            <Stack.Screen name="Timetable" component={TimetableScreen} options={{ headerShown: false }} />
            <Stack.Screen
              name="Events"
              component={EventsScreen}
              options={({ navigation }) => ({
                title: 'Events & Memos',
                headerRight: () => (
                  <TouchableOpacity onPress={() => navigation.navigate('Feedback')} style={{ paddingHorizontal: 4 }}>
                    <Text style={{ color: COLORS.blue, fontWeight: '700', fontSize: 13 }}>Feedback</Text>
                  </TouchableOpacity>
                ),
              })}
            />
            <Stack.Screen name="AddCourse" component={AddCourseScreen} options={{ title: 'Add a Course' }} />
            <Stack.Screen name="EventDetail" component={EventDetailScreen} options={{ title: 'Details' }} />
            <Stack.Screen name="QRShare" component={QRShareScreen} options={{ headerShown: false }} />
            <Stack.Screen name="QRScan" component={QRScanScreen} options={{ headerShown: false }} />
            <Stack.Screen
              name="SavedTimetables"
              component={SavedTimetablesScreen}
              options={{ title: 'Saved Timetables' }}
            />
            <Stack.Screen name="Settings" component={SettingsScreen} options={{ title: 'Settings' }} />
            <Stack.Screen name="Feedback" component={FeedbackScreen} options={{ title: 'Send Feedback' }} />
          </>
        )}
      </Stack.Navigator>
    </NavigationContainer>
  );
}
